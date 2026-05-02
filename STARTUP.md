# PlugHub Pilot — Startup Guide

Complete step-by-step instructions to bring up all services for a live presentation.
Run the steps below **in order**: infrastructure first, then backend services, then frontends.

---

## Demo Stack — ambiente completo para testes manuais

Um único comando sobe toda a plataforma (infra + aplicação + observabilidade):

```bash
# Na raiz do repositório:
docker compose -f docker-compose.demo.yml up --build --scale e2e-runner=0
```

`--scale e2e-runner=0` sobe a stack sem o runner de testes automáticos — ideal para uso interativo.

Aguarde todos os containers ficarem `healthy` (~3–5 min na primeira vez).

### URLs de acesso

| URL | O que é |
|---|---|
| **http://localhost:5173** | **Agent Assist UI** — interface do agente humano |
| **http://localhost:5173/webchat-test.html** | **WebChat cliente** — interface do consumidor final |
| **http://localhost:4000** | **Operator Console** — supervisão, analytics e configuração |
| http://localhost:9001 | Kafdrop — UI do Kafka |
| http://localhost:8081 | Redis Commander — UI do Redis |

### Fluxo de teste manual

**Atendimento básico (pool de IA):**
1. Abra o WebChat em `http://localhost:5173/webchat-test.html`
2. Selecione pool `sac_ia` e clique **Conectar**
3. Envie uma mensagem — o agente IA responde automaticamente
4. Acompanhe o heatmap em `http://localhost:4000` (painel Heatmap)

**Atendimento com agente humano:**
1. Abra o Agent Assist UI: `http://localhost:5173?pool=retencao_humano&agent=agente_humano_v1-001`
2. Abra o WebChat: `http://localhost:5173/webchat-test.html` → pool `retencao_humano`
3. Envie mensagens do cliente — o agente humano recebe em tempo real
4. Supervisor pode entrar pelo Operator Console → Heatmap → drill-down na sessão → **Entrar como supervisor**

**Co-pilot @mention:**
1. Abra o Agent Assist UI no pool `retencao_humano`
2. O agente humano digita `@copilot ativa` → co-pilot analisa e envia sugestão (`agents_only`)
3. `@copilot para` → co-pilot encerra

**Fluxo masked PIN (auth_ia):**
1. WebChat → pool `auth_ia`
2. O agente solicita PIN via campo mascarado (overlay no webchat)
3. PIN válido: 6 dígitos começando com "1" (ex: `123456`)

### Operator Console — painéis disponíveis

| Painel | Acesso | Admin token |
|---|---|---|
| Heatmap | automático | — |
| Workflows | botão nav | — |
| Campaigns | botão nav | — |
| Webhooks | botão nav | `demo_pricing_admin_token` |
| Registry | botão nav | — |
| Skills | botão nav | — |
| Channels | botão nav | — |
| Agents | botão nav | — |
| Config | botão nav | `demo_config_admin_token` |
| Pricing | botão nav | `demo_pricing_admin_token` |

### Tenant padrão

Todos os serviços usam `tenant_demo`. O campo TENANT no canto superior direito do Operator Console pode ser alterado para apontar para outro tenant (se cadastrado).

### Encerrar a stack

```bash
# Preserva volumes (dados)
docker compose -f docker-compose.demo.yml down

# Reset completo (apaga todos os dados)
docker compose -f docker-compose.demo.yml down -v
```

### Rodar os testes e2e automáticos

```bash
# Todos os cenários 01–14 (padrão --demo)
docker compose -f docker-compose.demo.yml up --build

# Cenário específico
E2E_EXTRA_ARGS="--only 07" docker compose -f docker-compose.demo.yml up --build

# Apenas webchat
E2E_EXTRA_ARGS="--webchat" docker compose -f docker-compose.demo.yml up --build
```

---

## Full Integration Stack — WebChat + Agentes + Supervisão

Stack completa para validar o fluxo end-to-end: cliente → canal → roteamento → agente IA → escalação → agente humano → supervisão.

```powershell
# Na raiz do repositório:
docker compose -f docker-compose.full.yml up --build
```

Aguarde todos os containers ficarem `healthy` (pode levar 3–5 min na primeira vez — o `data-seed` espera o registry estar saudável antes de criar os pools e instâncias).

### URLs do Full Stack

| URL | Serviço |
|---|---|
| **http://localhost:9090** | **WebChat Test Client** — inicie contatos aqui |
| **http://localhost:5173** | **Agent Assist UI** — interface do agente humano |
| http://localhost:5174 | Platform UI — Config, Monitor, Workflows |
| http://localhost:5080 | Operator Console — heatmap + supervisão |
| http://localhost:8010 | Channel Gateway (WebSocket) |
| http://localhost:3300 | Agent Registry |
| http://localhost:3100 | MCP Server (Agent Runtime) |
| http://localhost:3500 | Analytics API |
| http://localhost:3600 | Config API |
| http://localhost:3700 | Calendar API |
| http://localhost:3800 | Workflow API |
| http://localhost:3000 | Metabase (BI) |
| http://localhost:8080 | Kafka UI |
| http://localhost:8081 | Redis Commander |

### Fluxo básico de validação

1. **Abra o WebChat** em `http://localhost:9090`
2. Clique em **Gerar e preencher token** (gera JWT com tenant_demo)
3. Ajuste a URL para `ws://localhost:8010/ws/chat/demo_ia` (IVR com botões) ou `sac_ia` (LLM)
4. Clique **Conectar** — você verá o menu do agente IA
5. Para escalar para humano, escolha a opção de especialista
6. **Abra o Agent Assist UI** em `http://localhost:5173?agent=Carlos&pool=retencao_humano`
   - O agente humano verá a sessão escalada e pode assumir o atendimento
7. **Abra o Operator Console** em `http://localhost:5080`
   - Heatmap de sentimento + drill-down de sessões + botão Entrar como Supervisor

### Agent Assist UI — parâmetros URL

```
http://localhost:5173?agent=Carlos&pool=retencao_humano
http://localhost:5173?agent=Ana&pool=retencao_humano
```

### Encerrar a stack

```powershell
# Preserva volumes
docker compose -f docker-compose.full.yml down

# Apaga tudo incluindo volumes (reset completo)
docker compose -f docker-compose.full.yml down -v
```

---

## Visual Stack — Inicialização Rápida (Docker Compose)

A forma mais rápida de subir toda a plataforma para validação visual. Um único comando sobe todos os serviços.

```powershell
# Na raiz do repositório:
docker compose -f docker-compose.visual.yml up --build
```

Aguarde todos os containers ficarem `healthy` (pode levar 2–3 min na primeira vez).

### URLs disponíveis

| URL | Serviço |
|---|---|
| http://localhost:5174 | **Platform UI** — Monitor, Config Plataforma, Workflows |
| http://localhost:5080 | Operator Console — heatmap, sessões ao vivo, supervisor |
| http://localhost:8010 | Channel Gateway — WebSocket webchat + upload |
| http://localhost:9090 | WebChat Test Client |
| http://localhost:3500 | Analytics API |
| http://localhost:3600 | Config API |
| http://localhost:3700 | Calendar API |
| http://localhost:3800 | Workflow API |
| http://localhost:3300 | Agent Registry |
| http://localhost:3000 | Metabase (BI self-service) |
| http://localhost:8080 | Kafka UI |
| http://localhost:8081 | Redis Commander |

### Login na Platform UI

Acesse `http://localhost:5174`. Use qualquer credencial aceita pelo Agent Registry (veja seed abaixo).
O `tenantId` usado nos módulos é lido automaticamente da sessão JWT.

### Config Plataforma — token de admin

Em `/config/platform`, insira `demo_config_admin_token` no campo "Admin Token" para habilitar edição de namespaces.

### Verificar saúde dos containers

```powershell
docker compose -f docker-compose.visual.yml ps
```

Todos os serviços de aplicação devem aparecer como `healthy`.

### Encerrar a stack

```powershell
# Preserva volumes (dados)
docker compose -f docker-compose.visual.yml down

# Apaga tudo incluindo volumes
docker compose -f docker-compose.visual.yml down -v
```

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

## Setup dos pools — único procedimento para todos os cenários

Os 4 pools são sempre registrados juntos. O único parâmetro que muda entre os cenários de teste é a URL do `wscat`. O Agent Assist UI e o agente humano são sempre os mesmos.

| Pool | Agente | Descrição |
|---|---|---|
| `demo_ia` | `agente_demo_ia_v1` | IVR — menu de botões fixo, sem LLM |
| `sac_ia` | `agente_sac_ia_v1` | LLM — conversa real com análise de sentimento e escalada condicional |
| `fila_humano` | `agente_fila_v1` | Queue Agent — ativado automaticamente quando `retencao_humano` não tem agente disponível |
| `retencao_humano` | `agente_retencao_humano_v1` | Humano — único pool de atendimento humano; destino de todas as escaladas |

### Passo A — Registrar pools e tipos de agentes (uma vez)

```powershell
. .\scripts\set-env.ps1

# ── Pools ──────────────────────────────────────────────────────────────────
$routing = '{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}'

foreach ($pool in @(
  @{ id="demo_ia";       name="Demo IA — IVR";              sla=480000;  queue=$null },
  @{ id="sac_ia";        name="SAC IA — LLM";               sla=480000;  queue=$null },
  @{ id="fila_humano";   name="Fila Humano — Queue Agent";  sla=1800000; queue=$null },
  @{ id="retencao_humano"; name="Retenção — Humano";        sla=480000;
     queue='{"agent_type_id":"agente_fila_v1","max_wait_s":1800}' }
)) {
  $body = @{
    pool_id    = $pool.id
    name       = $pool.name
    channel_types = @("chat")
    sla_target_ms = $pool.sla
    routing_expression = $routing | ConvertFrom-Json
    supervisor_config  = if ($pool.id -eq "retencao_humano") { @{ enabled=$true } } else { $null }
    queue_config       = if ($pool.queue) { $pool.queue | ConvertFrom-Json } else { $null }
  } | ConvertTo-Json -Depth 5

  Invoke-RestMethod -Method POST http://localhost:3300/v1/pools `
    -ContentType "application/json" -Headers @{"x-tenant-id"="default"} -Body $body |
    Select-Object pool_id | Write-Host
}

# ── Agent types ────────────────────────────────────────────────────────────
# framework=plughub-native: Bridge carrega o YAML de skills/ pelo agent_type_id como fallback.
# framework=human: Bridge publica conversation.assigned via Redis pub/sub.

foreach ($at in @(
  @{ id="agente_demo_ia_v1";          name="Demo IA — IVR v1";        fw="plughub-native"; pools=@("demo_ia");         max=10; model="stateless" },
  @{ id="agente_sac_ia_v1";           name="SAC IA LLM v1";           fw="plughub-native"; pools=@("sac_ia");          max=10; model="stateless" },
  @{ id="agente_fila_v1";             name="Agente de Fila v1";       fw="plughub-native"; pools=@("fila_humano");     max=50; model="stateless" },
  @{ id="agente_retencao_humano_v1";  name="Atendente de Retenção";   fw="human";          pools=@("retencao_humano"); max=3;  model="stateful"  }
)) {
  $body = @{
    agent_type_id           = $at.id
    name                    = $at.name
    framework               = $at.fw
    pools                   = $at.pools
    skills                  = @()
    max_concurrent_sessions = $at.max
    execution_model         = $at.model
  } | ConvertTo-Json -Depth 5

  Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types `
    -ContentType "application/json" -Headers @{"x-tenant-id"="default"} -Body $body |
    Select-Object agent_type_id | Write-Host
}
```

### Passo B — Registrar instâncias no Redis (renovar a cada 1h se necessário)

```powershell
. .\scripts\set-env.ps1
$ts = (Get-Date -Format "o")

# Instâncias IA — stateless, TTL 1h, nenhum estado persistente
foreach ($inst in @(
  @{ id="demo-ia-001";   at="agente_demo_ia_v1";  pool="demo_ia";  max=10; model="stateless" },
  @{ id="sac-ia-001";    at="agente_sac_ia_v1";   pool="sac_ia";   max=10; model="stateless" },
  @{ id="fila-ia-001";   at="agente_fila_v1";     pool="fila_humano"; max=50; model="stateless" }
)) {
  $json = '{"instance_id":"' + $inst.id + '","agent_type_id":"' + $inst.at + '","tenant_id":"default",' +
          '"pool_id":"' + $inst.pool + '","pools":["' + $inst.pool + '"],' +
          '"execution_model":"' + $inst.model + '","max_concurrent":' + $inst.max + ',' +
          '"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
  docker exec plughub-redis redis-cli SET "default:instance:$($inst.id)" $json EX 3600
  docker exec plughub-redis redis-cli SADD "default:pool:$($inst.pool):instances" $inst.id
  docker exec plughub-redis redis-cli HSET "default:routing:instance:$($inst.id):meta" pools ('["' + $inst.pool + '"]') agent_type_id $inst.at

  # Pool config (TTL 24h)
  $poolJson = '{"pool_id":"' + $inst.pool + '","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}'
  docker exec plughub-redis redis-cli SET "default:pool_config:$($inst.pool)" $poolJson EX 86400
}

# Instância humana — retencao_humano
$humJson = '{"instance_id":"retencao-humano-001","agent_type_id":"agente_retencao_humano_v1","tenant_id":"default",' +
           '"pool_id":"retencao_humano","pools":["retencao_humano"],' +
           '"execution_model":"stateful","max_concurrent":3,' +
           '"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:retencao-humano-001" $humJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:retencao_humano:instances" "retencao-humano-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:retencao-humano-001:meta" pools '["retencao_humano"]' agent_type_id "agente_retencao_humano_v1"
docker exec plughub-redis redis-cli SET "default:pool_config:retencao_humano" '{"pool_id":"retencao_humano","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400
```

> Se o routing falhar com "no agents available", repita o bloco acima para renovar os TTLs.

### Passo C — Abrir o Agent Assist UI (sempre o mesmo)

```
http://localhost:5173?agent=Carlos&pool=retencao_humano
```

O UI fica no lobby. Recebe `conversation.assigned` automaticamente quando qualquer pool escala para `retencao_humano`.

### Passo D — Conectar como cliente (escolha o pool)

```powershell
npm install -g wscat   # instalar wscat se necessário

# IVR com menu de botões — sem LLM
wscat -c "ws://localhost:8010/ws/chat/demo_ia"

# Agente LLM — conversa real com análise de sentimento
wscat -c "ws://localhost:8010/ws/chat/sac_ia"
```

Ao conectar, o channel-gateway responde com:
```json
{"type":"connection.accepted","contact_id":"<UUID>","session_id":"<UUID>"}
```

Envio de mensagem de texto:
```json
{"type":"message.text","text":"Quero cancelar meu plano"}
```

Submissão de menu de botões (demo_ia):
```json
{"type":"menu.submit","menu_id":"menu_principal","interaction":"button","result":"especialista"}
```

### Testar o Queue Agent Pattern (fila)

Para ativar o `agente_fila_v1`, **não registre** a instância `retencao-humano-001` no Redis antes de conectar. Sem agente humano disponível, o Routing Engine publica em `conversations.queued` e o Orchestrator Bridge ativa o agente de fila automaticamente.

```powershell
# 1. Remover instância humana (simula ausência de atendentes)
docker exec plughub-redis redis-cli DEL "default:instance:retencao-humano-001"
docker exec plughub-redis redis-cli SREM "default:pool:retencao_humano:instances" "retencao-humano-001"

# 2. Conectar via sac_ia e enviar mensagem que acione escalada
wscat -c "ws://localhost:8010/ws/chat/sac_ia"
# → {"type":"message.text","text":"Preciso cancelar meu contrato urgente"}
# → agente_sac_ia_v1 decide escalar → Routing Engine não encontra humano → conversations.queued
# → agente_fila_v1 ativado → cliente recebe boas-vindas da fila e pode continuar enviando mensagens

# 3. Para liberar o cliente da fila (simula agente disponível), re-registrar a instância humana:
$ts = (Get-Date -Format "o")
$humJson = '{"instance_id":"retencao-humano-001","agent_type_id":"agente_retencao_humano_v1","tenant_id":"default","pool_id":"retencao_humano","pools":["retencao_humano"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:retencao-humano-001" $humJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:retencao_humano:instances" "retencao-humano-001"
# O kafka_listener detecta agent_ready → envia sinal __agent_available__ → agente_fila_v1 transfere
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
Publicado pelo **Channel Gateway** na conexão do cliente (com `pool_id` da URL)
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

### timeout_s nos menus

`agente_demo_ia_v1` usa `timeout_s: 120` (2 min no menu de botões). `agente_sac_ia_v1` usa `timeout_s: 300` (5 min de inatividade). `agente_fila_v1` usa `timeout_s: 0` (espera indefinida até `__agent_available__` ou desconexão).

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
| `scripts/linux/seed-demo.sh` | Seed unificado — todos os 4 pools + instâncias no Redis |
| `ecosystem.config.js` | Configuração PM2 (raiz do repositório) |

### Alternando entre pools

O `pool_id` é passado na URL do WebSocket — não é necessário reiniciar nada para testar pools diferentes:

```bash
wscat -c "ws://localhost:8010/ws/chat/demo_ia"      # IVR com menu de botões
wscat -c "ws://localhost:8010/ws/chat/sac_ia"       # LLM com análise de sentimento
```

O Agent Assist UI é sempre `http://localhost:5173?agent=Carlos&pool=retencao_humano`.

`PLUGHUB_ENTRY_POINT_POOL_ID` em `set-env.sh` é apenas fallback para deploys legados — deixe em branco em desenvolvimento.
