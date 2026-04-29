# seed-demo.ps1 — Seed dos 4 pools padronizados do PlugHub Demo (Windows/PowerShell)
#
# O que faz:
#   Registra pools e instâncias no Redis para que o Routing Engine
#   saiba que existem slots disponíveis.
#
# Uso (PowerShell, a partir da raiz do repo):
#   .\scripts\seed-demo.ps1
#
# Pré-requisitos:
#   - docker compose -f docker-compose.full.yml up (ou demo)
#   - Container Redis rodando como "full-redis" (full) ou "demo-redis" (demo)

param(
    [string]$TenantId     = "tenant_demo",
    [string]$RedisContainer = "",
    [string]$ComposeFile  = "docker-compose.full.yml"
)

$ErrorActionPreference = "Stop"

function Write-Info    { param($msg) Write-Host "[seed]  $msg" -ForegroundColor Cyan }
function Write-Ok      { param($msg) Write-Host "[ok]    $msg" -ForegroundColor Green }
function Write-Warning { param($msg) Write-Host "[warn]  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host "  PlugHub — Seed Demo (Redis agent instances)" -ForegroundColor White
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host ""
Write-Info "Tenant ID: $TenantId"

# Auto-detect Redis container name
# Priority: exact names first, then any container whose name contains "redis"
# but does NOT contain "commander" (redis-commander is not the server)
if (-not $RedisContainer) {
    $containers = (docker ps --format "{{.Names}}" 2>$null) -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $exactNames = @("full-redis", "demo-redis", "redis")
    foreach ($name in $exactNames) {
        if ($containers -contains $name) { $RedisContainer = $name; break }
    }
    if (-not $RedisContainer) {
        # Fallback: first container whose name contains "redis" but not "commander"
        $RedisContainer = $containers |
            Where-Object { $_ -like "*redis*" -and $_ -notlike "*commander*" } |
            Select-Object -First 1
    }
    if (-not $RedisContainer) {
        Write-Host "[error] Nenhum container Redis encontrado. Execute: docker compose -f $ComposeFile up -d redis" -ForegroundColor Red
        exit 1
    }
}
Write-Info "Redis container: $RedisContainer"
Write-Host ""

function Redis-Cmd {
    param([string[]]$args)
    $result = docker exec $RedisContainer redis-cli @args 2>&1
    return $result
}

$NOW = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$T = $TenantId
$ROUTING = '{"weight_sla":0.4,"weight_wait":0.2,"weight_tier":0.2,"weight_churn":0.1,"weight_business":0.1}'

Write-Info "Registrando pool configs no Redis..."

# Pool configs (TTL 24h)
foreach ($pool_id in @("demo_ia", "sac_ia", "fila_humano")) {
    $json = "{`"pool_id`":`"$pool_id`",`"tenant_id`":`"$T`",`"channel_types`":[`"webchat`",`"whatsapp`"],`"sla_target_ms`":300000,`"routing_expression`":$ROUTING,`"competency_weights`":{},`"aging_factor`":0.4,`"breach_factor`":0.8,`"remote_sites`":[],`"is_human_pool`":false}"
    docker exec $RedisContainer redis-cli SET "${T}:pool_config:${pool_id}" $json EX 86400 | Out-Null
    Write-Ok "Pool config $pool_id registrado"
}

$json_retencao = "{`"pool_id`":`"retencao_humano`",`"tenant_id`":`"$T`",`"channel_types`":[`"webchat`",`"whatsapp`"],`"sla_target_ms`":300000,`"routing_expression`":$ROUTING,`"competency_weights`":{},`"aging_factor`":0.4,`"breach_factor`":0.8,`"remote_sites`":[],`"is_human_pool`":true}"
docker exec $RedisContainer redis-cli SET "${T}:pool_config:retencao_humano" $json_retencao EX 86400 | Out-Null
Write-Ok "Pool config retencao_humano registrado"

# Pool sets
docker exec $RedisContainer redis-cli SADD "${T}:pools" "demo_ia" "sac_ia" "fila_humano" "retencao_humano" | Out-Null
Write-Ok "Pool set registrado: demo_ia, sac_ia, fila_humano, retencao_humano"

Write-Info "Registrando instâncias de agente..."

$INST_DEMO_IA  = "{`"instance_id`":`"demo-ia-001`",`"agent_type_id`":`"agente_demo_ia_v1`",`"tenant_id`":`"$T`",`"pool_id`":`"demo_ia`",`"pools`":[`"demo_ia`"],`"execution_model`":`"stateless`",`"max_concurrent`":10,`"current_sessions`":0,`"status`":`"ready`",`"registered_at`":`"$NOW`"}"
$INST_SAC_IA   = "{`"instance_id`":`"sac-ia-001`",`"agent_type_id`":`"agente_sac_ia_v1`",`"tenant_id`":`"$T`",`"pool_id`":`"sac_ia`",`"pools`":[`"sac_ia`"],`"execution_model`":`"stateless`",`"max_concurrent`":10,`"current_sessions`":0,`"status`":`"ready`",`"registered_at`":`"$NOW`"}"
$INST_FILA     = "{`"instance_id`":`"fila-ia-001`",`"agent_type_id`":`"agente_fila_v1`",`"tenant_id`":`"$T`",`"pool_id`":`"fila_humano`",`"pools`":[`"fila_humano`"],`"execution_model`":`"stateless`",`"max_concurrent`":50,`"current_sessions`":0,`"status`":`"ready`",`"registered_at`":`"$NOW`"}"
$INST_RETENCAO = "{`"instance_id`":`"retencao-humano-001`",`"agent_type_id`":`"agente_retencao_humano_v1`",`"tenant_id`":`"$T`",`"pool_id`":`"retencao_humano`",`"pools`":[`"retencao_humano`"],`"execution_model`":`"stateful`",`"max_concurrent`":3,`"current_sessions`":0,`"status`":`"ready`",`"registered_at`":`"$NOW`"}"

docker exec $RedisContainer redis-cli SET "${T}:instance:demo-ia-001"         $INST_DEMO_IA  | Out-Null; Write-Ok "Instância demo-ia-001 (pool: demo_ia)"
docker exec $RedisContainer redis-cli SET "${T}:instance:sac-ia-001"          $INST_SAC_IA   | Out-Null; Write-Ok "Instância sac-ia-001 (pool: sac_ia)"
docker exec $RedisContainer redis-cli SET "${T}:instance:fila-ia-001"         $INST_FILA     | Out-Null; Write-Ok "Instância fila-ia-001 (pool: fila_humano)"
docker exec $RedisContainer redis-cli SET "${T}:instance:retencao-humano-001" $INST_RETENCAO | Out-Null; Write-Ok "Instância retencao-humano-001 (pool: retencao_humano)"

# Templates permanentes (sem TTL — para auto-recovery)
docker exec $RedisContainer redis-cli SET "${T}:instance_template:demo-ia-001"         $INST_DEMO_IA  | Out-Null
docker exec $RedisContainer redis-cli SET "${T}:instance_template:sac-ia-001"          $INST_SAC_IA   | Out-Null
docker exec $RedisContainer redis-cli SET "${T}:instance_template:fila-ia-001"         $INST_FILA     | Out-Null
docker exec $RedisContainer redis-cli SET "${T}:instance_template:retencao-humano-001" $INST_RETENCAO | Out-Null
Write-Ok "Templates de instância gravados (sem TTL)"

# Pool rosters permanentes
docker exec $RedisContainer redis-cli SADD "${T}:pool_roster:demo_ia"         "demo-ia-001"          | Out-Null
docker exec $RedisContainer redis-cli SADD "${T}:pool_roster:sac_ia"          "sac-ia-001"           | Out-Null
docker exec $RedisContainer redis-cli SADD "${T}:pool_roster:fila_humano"     "fila-ia-001"          | Out-Null
docker exec $RedisContainer redis-cli SADD "${T}:pool_roster:retencao_humano" "retencao-humano-001"  | Out-Null
Write-Ok "Pool rosters gravados (sem TTL)"

# Pool instance sets
docker exec $RedisContainer redis-cli SADD "${T}:pool:demo_ia:instances"         "demo-ia-001"          | Out-Null; Write-Ok "Pool demo_ia:instances → demo-ia-001"
docker exec $RedisContainer redis-cli SADD "${T}:pool:sac_ia:instances"          "sac-ia-001"           | Out-Null; Write-Ok "Pool sac_ia:instances → sac-ia-001"
docker exec $RedisContainer redis-cli SADD "${T}:pool:fila_humano:instances"     "fila-ia-001"          | Out-Null; Write-Ok "Pool fila_humano:instances → fila-ia-001"
docker exec $RedisContainer redis-cli SADD "${T}:pool:retencao_humano:instances" "retencao-humano-001"  | Out-Null; Write-Ok "Pool retencao_humano:instances → retencao-humano-001"

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host "  Seed concluído!" -ForegroundColor Green
Write-Host ""
Write-Host "  Pools: demo_ia, sac_ia, fila_humano, retencao_humano" -ForegroundColor White
Write-Host ""
Write-Host "  Agent Assist UI  →  http://localhost:5173?agent=Carlos&pool=retencao_humano" -ForegroundColor Cyan
Write-Host "  WebChat client   →  ws://localhost:8010/ws/chat/retencao_humano  (JWT com tenant_demo)" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor White
Write-Host ""
