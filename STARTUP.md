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
# pool_id é passado via URL: ws://localhost:8010/ws/chat/{pool_id}
# PLUGHUB_ENTRY_POINT_POOL_ID é fallback para deploys legados sem pool_id na URL.
# Em desenvolvimento, deixe em branco e use a URL com pool_id para testar múltiplos pools:
#   ws://localhost:8010/ws/chat/demo_ia        # IVR fixo
#   ws://localhost:8010/ws/chat/sac_ia         # LLM
#   ws://localhost:8010/ws/chat/retencao_humano # Humano direto (testes)
$env:PLUGHUB_ENTRY_POINT_POOL_ID = ""
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

Acesse: `http://localhost:5173?agent=NOME&pool=retencao_humano`

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

## Pools padronizados (4 pools)

| Pool | Tipo | Skill/Agente | Descrição |
|---|---|---|---|
| `demo_ia` | IA (plughub-native) | `agente_demo_ia_v1.yaml` | IVR fixo com menu de botões — sem LLM |
| `sac_ia` | IA (plughub-native) | `agente_sac_ia_v1.yaml` | Agente LLM com análise de sentimento e escalada condicional |
| `fila_humano` | IA (plughub-native) | `agente_fila_v1.yaml` | Queue Agent — ativa quando `retencao_humano` não tem agentes disponíveis |
| `retencao_humano` | Humano | — | Atendimento humano; `queue_config` aponta para `fila_humano` |

Pools de IA e humano são **sempre separados** — garante que a escalação nunca re-aloca um agente IA para uma sessão já escalada para humano.

---

## Demo Completo 1 — Fluxo IVR (demo_ia → retencao_humano)

Este roteiro testa o fluxo ponta-a-ponta com o agente IVR de botões: cliente conecta → recebe menu → seleciona "Especialista" → escala para pool humano.

### Pré-requisitos

Todos os serviços dos passos 6.1–6.11 devem estar rodando, incluindo o **Orchestrator Bridge (6.10)**.

### Passo 1 — Registrar pools e tipos de agentes

Abra uma aba PowerShell e execute:

```powershell
. .\scripts\set-env.ps1

# 1a. Registrar pool IVR demo_ia
Invoke-RestMethod -Method POST http://localhost:3300/v1/pools -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "pool_id": "demo_ia",
  "name": "Demo IA — IVR",
  "channel_types": ["chat"],
  "sla_target_ms": 480000,
  "routing_expression": {
    "weight_sla": 1.0, "weight_wait": 0.8,
    "weight_tier": 0.6, "weight_churn": 0.9, "weight_business": 0.4
  }
}' | ConvertTo-Json

# 1b. Registrar pool humano retencao_humano
#     queue_config aponta para agente_fila_v1 — ativado quando não há humano disponível.
Invoke-RestMethod -Method POST http://localhost:3300/v1/pools -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "pool_id": "retencao_humano",
  "name": "Retenção — Humano",
  "channel_types": ["chat"],
  "sla_target_ms": 480000,
  "routing_expression": {
    "weight_sla": 1.0, "weight_wait": 0.8,
    "weight_tier": 0.6, "weight_churn": 0.9, "weight_business": 0.4
  },
  "supervisor_config": { "enabled": true },
  "queue_config": { "agent_type_id": "agente_fila_v1", "max_wait_s": 1800 }
}' | ConvertTo-Json

# 1c. Registrar tipo de agente IVR (framework=plughub-native → carrega agente_demo_ia_v1.yaml)
Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "agent_type_id": "agente_demo_ia_v1",
  "name": "Demo IA — IVR v1",
  "framework": "plughub-native",
  "pools": ["demo_ia"],
  "skills": [],
  "max_concurrent_sessions": 10,
  "execution_model": "stateless"
}' | ConvertTo-Json

# 1d. Registrar tipo de agente humano (framework=human → Bridge publica conversation.assigned)
Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "agent_type_id": "agente_retencao_humano_v1",
  "name": "Atendente de Retenção",
  "framework": "human",
  "pools": ["retencao_humano"],
  "skills": [],
  "max_concurrent_sessions": 3,
  "execution_model": "stateful"
}' | ConvertTo-Json
```

> **Nota:** o Orchestrator Bridge carrega automaticamente o YAML de fallback `packages/skill-flow-engine/skills/agente_demo_ia_v1.yaml` quando `skills: []` e nenhuma skill está registrada no Agent Registry para o `agent_type_id`. Não é necessário registrar a skill inline.

### Passo 2 — Registrar instâncias no Redis (para Routing Engine)

```powershell
. .\scripts\set-env.ps1
$ts = (Get-Date -Format "o")

# Pool demo_ia + instância do agente IVR (TTL 24h/1h)
docker exec plughub-redis redis-cli SET "default:pool_config:demo_ia" '{"pool_id":"demo_ia","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

$iaJson = '{"instance_id":"demo-ia-001","agent_type_id":"agente_demo_ia_v1","tenant_id":"default","pool_id":"demo_ia","pools":["demo_ia"],"execution_model":"stateless","max_concurrent":10,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:demo-ia-001" $iaJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:demo_ia:instances" "demo-ia-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:demo-ia-001:meta" pools '["demo_ia"]' agent_type_id "agente_demo_ia_v1"

# Pool retencao_humano + instância do agente humano (TTL 24h/1h)
docker exec plughub-redis redis-cli SET "default:pool_config:retencao_humano" '{"pool_id":"retencao_humano","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

$humJson = '{"instance_id":"retencao-humano-001","agent_type_id":"agente_retencao_humano_v1","tenant_id":"default","pool_id":"retencao_humano","pools":["retencao_humano"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:retencao-humano-001" $humJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:retencao_humano:instances" "retencao-humano-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:retencao-humano-001:meta" pools '["retencao_humano"]' agent_type_id "agente_retencao_humano_v1"
```

> **Importante:** as instâncias têm TTL de 1 hora. Se o routing falhar com "no agents available", repita o bloco acima para renovar as chaves.

### Passo 3 — Abrir o Agent Assist UI para o agente humano

Abra o Agent Assist UI **antes** de iniciar a conversa do cliente:

```
http://localhost:5173?agent=Carlos&pool=retencao_humano
```

O Agent Assist UI ficará aguardando o evento `conversation.assigned` que chega via Redis pub/sub quando o Routing Engine alocar a sessão para `retencao_humano`. O status deve aparecer como **connected**.

### Passo 4 — Simular cliente conectando via WebSocket

```powershell
# Instala wscat se não tiver
npm install -g wscat

# Conecta como cliente ao pool demo_ia (IVR)
wscat -c "ws://localhost:8010/ws/chat/demo_ia"
```

Ao conectar, o channel-gateway responde com:
```json
{"type":"connection.accepted","contact_id":"<UUID>","session_id":"<UUID>"}
```

**Neste momento o channel-gateway já publica automaticamente o `ConversationInboundEvent` com `pool_id: "demo_ia"` para o Routing Engine** — não é necessário publicar manualmente.

Logo em seguida a IA envia o menu com 3 botões. Escolha "especialista" para acionar a escalada:
```json
{"type":"menu.submit","menu_id":"menu_principal","interaction":"button","result":"especialista"}
```

### Passo 5 — Observar o fluxo automático

Monitore os logs das abas dos serviços. A sequência esperada é:

1. **Channel Gateway** → `routing_event published: session=<ID> pool=demo_ia`
2. **Routing Engine** → `Routed session=<ID> → instance=demo-ia-001 pool=demo_ia`
3. **Orchestrator Bridge** → `Routing: session=<ID> agent=agente_demo_ia_v1 pool=demo_ia framework=plughub-native`
4. **Skill Flow Service** → carrega `agente_demo_ia_v1.yaml` (fallback), executa step `menu_principal`
5. **MCP Server** → `notification_send` → envia menu de botões ao cliente via WebSocket
6. **Skill Flow Service** → cliente seleciona "especialista" → step `avisar_especialista` → step `escalar`
7. **MCP Server** → `conversation_escalate` → publica em `conversations.inbound` com `pool_id: retencao_humano`
8. **Routing Engine** → `Routed session=<ID> → instance=retencao-humano-001 pool=retencao_humano`
9. **Orchestrator Bridge** → `framework=human` → publica `conversation.assigned` via Redis pub/sub
10. **Agent Assist UI** (aba do navegador) → recebe evento `conversation.assigned`

### Passo 6 — Conversar como agente humano

Após o `conversation.assigned` aparecer no Agent Assist UI, o agente humano pode enviar mensagens diretamente pelo campo de texto.

Para enviar uma mensagem como cliente no `wscat` (enquanto aguarda o menu ou depois de escalado):
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

## Demo Completo 2 — Fluxo LLM com Sentimento em Tempo Real (sac_ia)

Este roteiro usa `agente_sac_ia_v1.yaml` — agente LLM que conversa com o cliente via `reason` step, analisa sentimento e intenção, e escala condicionalmente para `retencao_humano`.

### Diferença em relação ao Demo 1

| | Demo 1 (`agente_demo_ia_v1`) | Demo 2 (`agente_sac_ia_v1`) |
|---|---|---|
| Pool | `demo_ia` | `sac_ia` |
| Interação IA | Menu de botões fixo (IVR) | Loop de conversa real via LLM |
| Sentimento | Não alimentado | Atualizado a cada turno pelo AI Gateway |
| Escalação | Opção "especialista" no menu | Condicional — só se `escalar=true` na análise |
| Seed | Manual (passos 1–2) | Registra pool `sac_ia` + agent type |

### Pré-requisitos

Todos os serviços dos passos 6.1–6.11 devem estar rodando.
`PLUGHUB_ANTHROPIC_API_KEY` deve estar configurada — o step `reason` chama o AI Gateway.

### Passo 1 — Registrar pool sac_ia e tipo de agente

```powershell
. .\scripts\set-env.ps1

# Pool sac_ia (LLM)
Invoke-RestMethod -Method POST http://localhost:3300/v1/pools -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "pool_id": "sac_ia",
  "name": "SAC IA — LLM",
  "channel_types": ["chat"],
  "sla_target_ms": 480000,
  "routing_expression": {
    "weight_sla": 1.0, "weight_wait": 0.8,
    "weight_tier": 0.6, "weight_churn": 0.9, "weight_business": 0.4
  }
}' | ConvertTo-Json

# Tipo de agente LLM (carrega agente_sac_ia_v1.yaml como fallback)
Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "agent_type_id": "agente_sac_ia_v1",
  "name": "SAC IA LLM v1",
  "framework": "plughub-native",
  "pools": ["sac_ia"],
  "skills": [],
  "max_concurrent_sessions": 10,
  "execution_model": "stateless"
}' | ConvertTo-Json
```

> O pool `retencao_humano` e seu agent type já foram registrados no Demo 1 — não precisa repetir.

### Passo 2 — Registrar instâncias no Redis

```powershell
. .\scripts\set-env.ps1
$ts = (Get-Date -Format "o")

# Pool sac_ia + instância
docker exec plughub-redis redis-cli SET "default:pool_config:sac_ia" `
  '{"pool_id":"sac_ia","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

$iaJson = '{"instance_id":"sac-ia-001","agent_type_id":"agente_sac_ia_v1","tenant_id":"default","pool_id":"sac_ia","pools":["sac_ia"],"execution_model":"stateless","max_concurrent":10,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:sac-ia-001" $iaJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:sac_ia:instances" "sac-ia-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:sac-ia-001:meta" pools '["sac_ia"]' agent_type_id "agente_sac_ia_v1"

# Renovar instância humana de retencao_humano se necessário (TTL 1h)
$humJson = '{"instance_id":"retencao-humano-001","agent_type_id":"agente_retencao_humano_v1","tenant_id":"default","pool_id":"retencao_humano","pools":["retencao_humano"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:retencao-humano-001" $humJson EX 3600
```

### Passo 3 — Abrir o Agent Assist UI

```
http://localhost:5173?agent=Carlos&pool=retencao_humano
```

O UI fica no lobby aguardando `conversation.assigned`. Quando `agente_sac_ia_v1` decidir escalar (`escalar=true`), a sessão é atribuída automaticamente ao `retencao_humano`.

### Passo 4 — Simular cliente e conversar com a IA

```powershell
wscat -c "ws://localhost:8010/ws/chat/sac_ia"
```

Responda ao prompt da IA com texto livre:
```json
{"type":"message.text","text":"Quero cancelar meu plano"}
```

O Skill Flow executa o loop: `aguardar_mensagem` → `analisar` (AI Gateway reason step) → `responder` ou `avisar_escalada` → `escalar`.

### O que observar no Agent Assist UI

- **Strip de sentimento** (acima das mensagens): ponto colorido + percentual + tendência + intenção + flags
- **Aba Estado**: gráfico de trajetória do sentimento por turno, histórico de intenções
- **Histórico completo**: cliente (cinza), IA (violeta), agente humano (azul), sistema/menu (âmbar)
- **Menu render**: quando a IA envia botões, as opções aparecem listadas na mensagem de sistema

### Nota sobre timeout_s

O campo `timeout_ms` foi renomeado para `timeout_s` (segundos, inteiro >= 0). O valor `0` significa espera indefinida — o menu só desbloqueia quando o cliente responde ou desconecta. `agente_sac_ia_v1` usa `timeout_s: 300` (5 min de inatividade) e `agente_fila_v1` usa `timeout_s: 0` (espera indefinida até `__agent_available__`).

---

## Demo Completo 3 — Queue Agent Pattern (fila_humano)

Este roteiro demonstra o Queue Agent Pattern: cliente conecta via `sac_ia` → IA decide escalar → `retencao_humano` não tem agentes disponíveis → Routing Engine publica em `conversations.queued` → Orchestrator Bridge ativa `agente_fila_v1` → cliente interage com o agente de fila enquanto aguarda → quando agente humano fica disponível, `agente_fila_v1` escalona para `retencao_humano`.

### Pré-requisitos

Todos os serviços dos passos 6.1–6.11 devem estar rodando, incluindo o **Orchestrator Bridge (6.10)**.
Os pools `sac_ia` e `retencao_humano` já devem estar registrados (Demos 1 e 2).
`PLUGHUB_ANTHROPIC_API_KEY` configurada — `agente_fila_v1` usa o AI Gateway para gerar respostas de fila.

### Passo 1 — Registrar pool fila_humano e tipo de agente de fila

```powershell
. .\scripts\set-env.ps1

# Pool fila_humano — usado como staging do agente de fila
Invoke-RestMethod -Method POST http://localhost:3300/v1/pools -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "pool_id": "fila_humano",
  "name": "Fila Humano — Queue Agent",
  "channel_types": ["chat"],
  "sla_target_ms": 1800000,
  "routing_expression": {
    "weight_sla": 1.0, "weight_wait": 0.8,
    "weight_tier": 0.6, "weight_churn": 0.9, "weight_business": 0.4
  }
}' | ConvertTo-Json

# Tipo de agente de fila (carrega agente_fila_v1.yaml como fallback)
Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "agent_type_id": "agente_fila_v1",
  "name": "Agente de Fila v1",
  "framework": "plughub-native",
  "pools": ["fila_humano"],
  "skills": [],
  "max_concurrent_sessions": 50,
  "execution_model": "stateless"
}' | ConvertTo-Json
```

### Passo 2 — Testar o Queue Agent

Para simular fila (sem agente humano disponível), NÃO registre a instância `retencao-humano-001` no Redis. Sem instâncias no pool, o Routing Engine publicará em `conversations.queued` e o Orchestrator Bridge ativará `agente_fila_v1`.

```powershell
# Conectar como cliente ao SAC IA
wscat -c "ws://localhost:8010/ws/chat/sac_ia"

# Enviar mensagem que acione escalada
{"type":"message.text","text":"Preciso cancelar meu contrato urgente"}
```

Sequência esperada:
1. `agente_sac_ia_v1` analisa → `escalar=true` → step `escalar` → `retencao_humano`
2. Routing Engine não encontra instâncias em `retencao_humano` → publica em `conversations.queued`
3. Orchestrator Bridge consome `conversations.queued` → ativa `agente_fila_v1` com `extra_context.pool_id=retencao_humano`
4. Cliente recebe: "Olá! No momento todos os especialistas estão ocupados..."
5. Cliente pode enviar mensagens — `agente_fila_v1` responde com IA

Para simular agente humano disponível:
```powershell
$ts = (Get-Date -Format "o")
$humJson = '{"instance_id":"retencao-humano-001","agent_type_id":"agente_retencao_humano_v1","tenant_id":"default","pool_id":"retencao_humano","pools":["retencao_humano"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:retencao-humano-001" $humJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:retencao_humano:instances" "retencao-humano-001"
# Publicar sinal de disponibilidade (kafka_listener faz isso automaticamente quando agent_ready chega)
# o cliente receberá: "Ótima notícia! Um especialista está disponível..."
```

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

Registra os pools `demo_ia`, `sac_ia` e `retencao_humano`, os AgentTypes e as instâncias no Redis.

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

### Alternando entre demos

O `pool_id` agora é passado diretamente na URL do WebSocket — não é mais necessário reiniciar o `channel-gateway` para testar pools diferentes:

```bash
# Demo 1 — IVR com menu de botões
wscat -c "ws://localhost:8010/ws/chat/demo_ia"

# Demo 2 — Agente LLM com sentimento
wscat -c "ws://localhost:8010/ws/chat/sac_ia"

# Demo 3 — Fila direta (testa Queue Agent Pattern)
wscat -c "ws://localhost:8010/ws/chat/sac_ia"   # escala → retencao_humano sem instâncias → fila
```

O `PLUGHUB_ENTRY_POINT_POOL_ID` em `set-env.sh` é apenas um fallback para deploys legados. Para desenvolvimento, deixe em branco:

```bash
export PLUGHUB_ENTRY_POINT_POOL_ID=""
```
