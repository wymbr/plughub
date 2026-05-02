# PlugHub Demo — Roteiro de Testes

> Ambiente: `docker compose -f docker-compose.demo.yml`  
> Data: Abril 2026 | Versão: Arc 7 completo

---

## 0. Pré-requisitos

### 0.1 Fix obrigatório: rebuild platform-ui (correção de role)

O bug `primaryRole` fazia `admin+developer → developer`, escondendo o menu Configuração.
Já corrigido no código — rebuild necessário:

```bash
docker compose -f docker-compose.demo.yml build platform-ui
docker compose -f docker-compose.demo.yml up -d platform-ui
```

### 0.2 Verificar stack completa

```bash
docker compose -f docker-compose.demo.yml ps
```

Serviços esperados como `Up`:

| Serviço | Porta (host) |
|---|---|
| platform-ui | 5174 |
| auth-api | **3202** (container: 3200) |
| agent-registry | 3300 |
| evaluation-api | 3400 |
| analytics-api | 3500 |
| config-api | 3600 |
| calendar-api | 3700 |
| workflow-api | 3800 |
| pricing-api | 3900 |
| mcp-server-plughub | 3100 |
| channel-gateway / webchat | 8010 |
| postgres | 5432 |
| redis | 6379 |
| kafka | 9092 |
| clickhouse | 8123 |

### 0.3 Criar usuários de teste

Execute estes `curl` para criar os usuários necessários:

**Nota:** auth-api exposta na porta **3202** no host (mapeamento `3202:3200` no docker-compose.demo.yml).

**Linux/macOS (bash):**
```bash
BASE=http://localhost:3202
TOKEN=changeme_auth_admin_token_demo

curl -s -X POST $BASE/auth/users -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant_demo","email":"supervisor@plughub.local","password":"changeme123","name":"Supervisor Demo","roles":["supervisor"],"accessible_pools":[]}'

curl -s -X POST $BASE/auth/users -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant_demo","email":"operador@plughub.local","password":"changeme123","name":"Operador Demo","roles":["operator"],"accessible_pools":[]}'

curl -s -X POST $BASE/auth/users -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant_demo","email":"business@plughub.local","password":"changeme123","name":"Business Demo","roles":["business"],"accessible_pools":[]}'
```

**Windows (PowerShell) — use arquivo temporário para evitar escape de aspas:**
```powershell
$TOKEN = "changeme_auth_admin_token_demo"
$BASE  = "http://localhost:3202"

foreach ($u in @(
  '{"tenant_id":"tenant_demo","email":"supervisor@plughub.local","password":"changeme123","name":"Supervisor Demo","roles":["supervisor"],"accessible_pools":[]}',
  '{"tenant_id":"tenant_demo","email":"operador@plughub.local","password":"changeme123","name":"Operador Demo","roles":["operator"],"accessible_pools":[]}',
  '{"tenant_id":"tenant_demo","email":"business@plughub.local","password":"changeme123","name":"Business Demo","roles":["business"],"accessible_pools":[]}'
)) {
  $u | Out-File -Encoding utf8 "$env:TEMP\pu.json"
  curl.exe -s -X POST "$BASE/auth/users" -H "X-Admin-Token: $TOKEN" -H "Content-Type: application/json" --data-binary "@$env:TEMP\pu.json"
  Write-Host ""
}
```

### 0.4 Mapa de roles × menus disponíveis

| Role | Menus visíveis |
|---|---|
| `admin` (seed) | Home, Atendimento, Workflows, Avaliação (completo), Analytics, Skill Flows, Configuração (completo), Developer |
| `supervisor` | Home, Atendimento, Workflows, Avaliação (sem Forms/Knowledge/Permissions), Analytics |
| `operator` | Home, Atendimento, Workflows, Avaliação (só Minhas), Analytics |
| `business` | Home, Analytics (Dashboards/Relatórios/Campanhas), Business |

---

## 1. Autenticação (Auth API)

**Usuário:** `admin@plughub.local` / `changeme_admin`  
**URL:** `http://localhost:5174/login`

| # | Passo | Esperado |
|---|---|---|
| 1.1 | Acesse `/login` sem estar logado | Formulário de login |
| 1.2 | Tente login com senha errada | Mensagem "Credenciais inválidas" |
| 1.3 | Login com `admin@plughub.local / changeme_admin` | Redireciona para Home. Badge "admin" no topo |
| 1.4 | Recarregue a página (F5) | Permanece logado (silent re-auth via refresh token) |
| 1.5 | Clique "Sair" | Redireciona para `/login`. localStorage limpo |
| 1.6 | Acesse `/skill-flows` sem login | Redireciona para `/login` |
| 1.7 | Login como `supervisor@plughub.local / changeme123` | Badge "supervisor". Menus diferentes |

**Verificação via API:**
```bash
# auth-api está na porta 3202 no host
curl -s -X POST http://localhost:3202/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@plughub.local","password":"changeme_admin","tenant_id":"tenant_demo"}' \
  | python3 -m json.tool | grep -E "access_token|roles|name"
```

---

## 2. Home

**Usuário:** qualquer

| # | Passo | Esperado |
|---|---|---|
| 2.1 | Acesse `/` | Exibe "Bem-vindo, [nome]" |
| 2.2 | Verifique campos Profile: USER ID, ROLE, TENANT, INSTALLATION | Valores corretos para o usuário logado |
| 2.3 | Quick Links mostra "Modules under construction" | OK (placeholder até Arc completo) |
| 2.4 | Platform Features: Routing Engine, Skill Flows, Analytics | Cards visíveis |

---

## 3. Skill Flows Editor

**Usuário:** `admin` ou `developer`  
**URL:** `http://localhost:5174/skill-flows`

| # | Passo | Esperado |
|---|---|---|
| 3.1 | Acesse `/skill-flows` | Sidebar com lista de skills. Editor Monaco à direita |
| 3.2 | Clique em `skill_auth_form_v1` | YAML carregado no editor |
| 3.3 | Clique em `skill_avaliacao_v1` | YAML diferente carregado |
| 3.4 | Edite um campo (ex: altere `version: '1.0'` para `'1.1'`) | Indicador `●` aparece no header |
| 3.5 | Pressione `⌘S` (ou clique Salvar) | Status bar: "saved". PUT enviado para agent-registry |
| 3.6 | Clique "Descartar" | YAML volta ao estado salvo. Indicador `●` some |
| 3.7 | Clique "+ Nova Skill" | Prompt pedindo skill_id |
| 3.8 | Digite `skill_teste_e2e_v1` e confirme | Template em branco carregado no editor |
| 3.9 | Salve a nova skill | Status "saved". Aparece na lista |
| 3.10 | Clique "Remover" → Confirmar | Skill removida da lista |
| 3.11 | YAML inválido (ex: `steps: [[[`) → Salvar | Status bar: "parse_error". PUT não enviado |

**Skills presentes (verificar lista):**
- skill_auth_form_v1, skill_auth_ia_v1
- skill_avaliacao_v1
- skill_contexto_ia_v1
- skill_copilot_sac_v1
- skill_demo_ia_v1
- skill_finalizacao_v1
- skill_retencao_v1
- skill_reviewer_ia_v1

---

## 4. Configuração → Recursos

**Usuário:** `admin`  
**URL:** `http://localhost:5174/config/recursos`

### 4.1 Pools

| # | Passo | Esperado |
|---|---|---|
| 4.1.1 | Acesse aba "Pools" | Lista de pools: sac, retencao_humano, avaliacao, etc. |
| 4.1.2 | Clique em um pool | Detalhe: canal, SLA, descrição |
| 4.1.3 | Crie pool: `pool_teste` channel=webchat, SLA=60000 | Aparece na lista |
| 4.1.4 | Delete `pool_teste` | Some da lista |

### 4.2 Agent Types

| # | Passo | Esperado |
|---|---|---|
| 4.2.1 | Aba "Agent Types" | Lista de agentes: agente_demo_ia_v1, etc. |
| 4.2.2 | Verifique framework e role de cada agente | Corretos |

### 4.3 Instâncias

| # | Passo | Esperado |
|---|---|---|
| 4.3.1 | Aba "Instances" | Lista de instâncias ativas. Status: ready/busy |
| 4.3.2 | Aguarde 15s | Auto-refresh atualiza a lista |

### 4.4 Canais (Channels)

| # | Passo | Esperado |
|---|---|---|
| 4.4.1 | Aba "Canais" | Lista de gateway configs por tipo de canal |
| 4.4.2 | Crie config WhatsApp com dados fictícios | Aparece na lista. Credentials mascarados (••••) |

---

## 5. Configuração → Plataforma

**Usuário:** `admin`  
**URL:** `http://localhost:5174/config/platform`

| # | Passo | Esperado |
|---|---|---|
| 5.1 | Acesse a página | Sidebar com namespaces: sentiment, routing, session, etc. |
| 5.2 | Clique em namespace `routing` | Chaves: performance_score_weight, sla_default_ms, etc. |
| 5.3 | Edite `performance_score_weight`: valor `0.3` → escopo Global | Campo salvo. Badge "global" |
| 5.4 | Edite mesma chave com scope "Tenant" (tenant_demo) | Badge "tenant override" aparece |
| 5.5 | Botão Reset | Override removido. Volta ao global |
| 5.6 | Namespace `quota` | Chaves: max_concurrent_sessions, llm_tokens_daily |

---

## 6. Configuração → Mascaramento

**Usuário:** `admin`  
**URL:** `http://localhost:5174/config/masking`

| # | Passo | Esperado |
|---|---|---|
| 6.1 | Acesse a página | 4 seções: Controle de Acesso, Audit Capture, Retenção, Categorias |
| 6.2 | Verifique authorized_roles | ["evaluator", "reviewer"] |
| 6.3 | Verifique default_retention_days | 90 (ou configurado) |

---

## 7. Configuração → Faturamento (Pricing)

**Usuário:** `admin`  
**URL:** `http://localhost:5174/config/billing`

| # | Passo | Esperado |
|---|---|---|
| 7.1 | Acesse a página | Sidebar com recursos (base + reserve). Tabs Invoice / Consumption |
| 7.2 | Tab Invoice | Tabela de base items com preços. Grand Total calculado |
| 7.3 | Botão Export XLSX | Download da fatura em Excel |
| 7.4 | Tab Consumption | Dimensões de uso (não incluídas no faturamento — só curadoria) |

**Verificação via API:**
```bash
curl -s "http://localhost:3900/v1/pricing/invoice/tenant_demo" | python3 -m json.tool
```

---

## 8. Avaliação

**Usuário:** `admin` (todos os sub-módulos)

### 8.1 Formulários (Forms)

**URL:** `http://localhost:5174/evaluation/forms`

| # | Passo | Esperado |
|---|---|---|
| 8.1.1 | Acesse a página | Lista de formulários cadastrados |
| 8.1.2 | Crie formulário com 2 critérios: pass_fail + score | form_id retornado |
| 8.1.3 | Edite nome do formulário | Atualizado |
| 8.1.4 | Delete o formulário criado | Some da lista |

### 8.2 Campanhas

**URL:** `http://localhost:5174/evaluation/campaigns`

| # | Passo | Esperado |
|---|---|---|
| 8.2.1 | Acesse a página | KPI bar + lista de campanhas |
| 8.2.2 | Crie campanha: mode=random, sample_rate=0.5 | Aparece na lista com status active |
| 8.2.3 | Pause a campanha | Status → paused |
| 8.2.4 | Resume | Status → active |

### 8.3 Base de Conhecimento (Knowledge)

**URL:** `http://localhost:5174/evaluation/knowledge`

| # | Passo | Esperado |
|---|---|---|
| 8.3.1 | Acesse a página | Campo de busca semântica |
| 8.3.2 | Busque "protocolo de saudação" | Snippets relevantes (se carregados) |

### 8.4 Fila de Revisão (Review)

**URL:** `http://localhost:5174/evaluation/review`  
**Usuário:** `supervisor`

| # | Passo | Esperado |
|---|---|---|
| 8.4.1 | Acesse a página | Lista de resultados com action_required=review |
| 8.4.2 | Abra um resultado | available_actions calculado server-side |

### 8.5 Permissões de Avaliação

**URL:** `http://localhost:5174/evaluation/permissions`

| # | Passo | Esperado |
|---|---|---|
| 8.5.1 | Acesse a página | Tabela de permissões 2D (usuário × scope) |
| 8.5.2 | Conceda can_review ao supervisor no escopo global | Linha aparece na tabela |
| 8.5.3 | Revogue | Some da tabela |

### 8.6 Relatórios de Avaliação

**URL:** `http://localhost:5174/evaluation/reports`  
**Usuário:** `supervisor` ou `admin`

| # | Passo | Esperado |
|---|---|---|
| 8.6.1 | Acesse a página | Dashboard com métricas de avaliação (ClickHouse) |
| 8.6.2 | Filtre por campaign_id | Resultados filtrados |

---

## 9. Analytics

**Usuário:** `supervisor`, `admin` ou `business`

### 9.1 Dashboards

**URL:** `http://localhost:5174/dashboards`

| # | Passo | Esperado |
|---|---|---|
| 9.1.1 | Acesse a página | Heatmap de sentimento por pool. Tiles coloridos |
| 9.1.2 | Clique em um pool | Drill-down: sessões ativas no pool |
| 9.1.3 | Clique em uma sessão | Transcrição ao vivo via SSE |

### 9.2 Relatórios

**URL:** `http://localhost:5174/reports`

| # | Passo | Esperado |
|---|---|---|
| 9.2.1 | Acesse a página | Relatórios de sessões, agentes, qualidade |
| 9.2.2 | Filtre por canal / outcome | Dados filtrados |
| 9.2.3 | Export CSV | Download disponível |

### 9.3 Campanhas (Collect)

**URL:** `http://localhost:5174/campaigns`

| # | Passo | Esperado |
|---|---|---|
| 9.3.1 | Acesse a página | KPI bar (Campanhas / Total / Taxa) + lista de campanhas |
| 9.3.2 | Selecione uma campanha | Detalhe: KPI grid, status bar, eventos recentes |

---

## 10. Workflows

**Usuário:** `supervisor` ou `admin`  
**URL:** `http://localhost:5174/workflows`

| # | Passo | Esperado |
|---|---|---|
| 10.1 | Acesse a página | Tabs: Instâncias / Webhooks |
| 10.2 | Tab Instâncias | Lista de workflow instances com status |
| 10.3 | Cancele uma instância active | Status → cancelled |
| 10.4 | Tab Webhooks | Lista de webhooks registrados |
| 10.5 | Crie webhook para flow `skill_demo_ia_v1` | Plain token exibido uma vez. Botão "I've saved it" |
| 10.6 | Copie a URL pública do webhook | Formato `POST /v1/workflow/webhook/{id}` |
| 10.7 | Dispare o webhook via curl | Status 202. Instance criada. Log de delivery aparece |

```bash
# Disparar webhook (substitua WEBHOOK_ID e TOKEN pelo retornado na criação)
curl -s -X POST "http://localhost:3800/v1/workflow/webhook/WEBHOOK_ID" \
  -H "X-Webhook-Token: TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"context_override":{"test":true}}'
```

---

## 11. Atendimento → Agent Assist

**Usuário:** `operator`  
**URL:** `http://localhost:5174/agent-assist`

| # | Passo | Esperado |
|---|---|---|
| 11.1 | Acesse a página | Seletor de pool. Escolha `sac` |
| 11.2 | Status WS: ponto verde no header | WebSocket conectado |
| 11.3 | Aguarde atribuição de sessão | `conversation.assigned` via WS |
| 11.4 | Envie mensagem no chat | Mensagem aparece na transcrição |
| 11.5 | Aba "Contexto" (direita) | ContextStore do cliente (nome, CPF, motivo) |
| 11.6 | Aba "Estado" | Sentimento atual, intent, flags de SLA |
| 11.7 | Clique "Encerrar" | Modal: issue_status + outcome |
| 11.8 | Confirme encerramento | Sessão fechada. Volta ao lobby |

---

## 12. Health Checks — APIs diretas

Execute para verificar todos os serviços:

```bash
#!/bin/bash
echo "=== PlugHub Health Check ==="

check() {
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$1" 2>/dev/null)
  printf "%-40s %s\n" "$2" "$STATUS"
}

check "http://localhost:3200/health"           "auth-api"
check "http://localhost:3300/health"           "agent-registry"
check "http://localhost:3400/health"           "evaluation-api"
check "http://localhost:3500/health"           "analytics-api"
check "http://localhost:3600/health"           "config-api"
check "http://localhost:3700/health"           "calendar-api"
check "http://localhost:3800/health"           "workflow-api"
check "http://localhost:3900/health"           "pricing-api"
check "http://localhost:3100/health"           "mcp-server-plughub"
check "http://localhost:8010/health"           "channel-gateway"
check "http://localhost:5174/"                 "platform-ui"
```

---

## 13. E2E Test Runner

Execute os cenários do runner TypeScript (a partir do diretório `packages/e2e-tests`):

```bash
cd packages/e2e-tests
npm install

# Cenários básicos (sempre rodar primeiro)
ts-node runner.ts --only 01   # Happy path — ciclo completo de sessão
ts-node runner.ts --only 02   # Escalação + handoff
ts-node runner.ts --only 03   # Resume após falha

# Webchat e upload
ts-node runner.ts --only 12   # Auth WS + upload de arquivo

# Workflow Automation (Arc 4)
ts-node runner.ts --only 13   # Trigger + suspend + resume + complete
ts-node runner.ts --only 14   # Collect step + campaign

# Analytics e segmentos
ts-node runner.ts --only 23   # ContactSegment pipeline

# Avaliação (Arc 6)
ts-node runner.ts --only 24   # Formulário + campanha + Kafka → ClickHouse
ts-node runner.ts --only 25   # Contestação + revisão humana
ts-node runner.ts --only 26   # AI Gateway fallback (requer ANTHROPIC_API_KEY)
ts-node runner.ts --only 27   # Permissões 2D
ts-node runner.ts --only 28   # Ciclo workflow de revisão (requer JWT_SECRET)

# Auth e Bootstrap
ts-node runner.ts --only 15   # Instance bootstrap (--bootstrap)
ts-node runner.ts --only 17   # ContextStore (--ctx)

# Suite completa (exceto cenários que exigem API key)
ts-node runner.ts
```

**Variáveis de ambiente necessárias para cenários avançados:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # cenário 26 (inference real)
export JWT_SECRET="changeme_auth_jwt_secret_at_least_32_chars"  # cenário 28
```

---

## 14. Pontos de Atenção e Bugs Conhecidos

| Item | Status | Observação |
|---|---|---|
| `primaryRole` no AuthContext | ✅ Corrigido | Admin agora tem acesso ao menu Configuração após rebuild |
| Developer /developer route | ⚠️ Placeholder | "Módulo em construção — Arc 4" |
| Monitor /monitor | ⚠️ Placeholder | Previsto em Arc posterior |
| Business /business | ⚠️ Placeholder | Role business não tem módulos completos ainda |
| ClickHouse — ReplacingMergeTree | ⚠️ Delay | Usar `FINAL` nas queries ou aguardar ~1s após inserção para ver dados consolidados |
| Metabase | ℹ️ Separado | Porta 3000 — BI self-service (requer `docker-compose.infra.yml`) |
| ANTHROPIC_API_KEY | ℹ️ Opcional | Sem ela, cenário 26 e Agent Assist IA degradam graciosamente |

---

## 15. Checklist Final

- [ ] Todos os serviços Up (health checks 200)
- [ ] Login admin → badge "admin" no topo
- [ ] Sidebar exibe todos os menus esperados por role
- [ ] Skill Flows: listar, editar, salvar, criar, remover
- [ ] Configuração → Plataforma: editar namespace routing
- [ ] Configuração → Faturamento: invoice carregada
- [ ] Avaliação: criar formulário e campanha
- [ ] Workflows: criar webhook e disparar
- [ ] Health checks: todos retornam 200
- [ ] E2E cenários 01, 02, 03 passando
- [ ] E2E cenários 24, 25, 27 passando (arc 6)
