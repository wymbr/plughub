# Módulo: Configuração → Plataforma

> Rota UI: `/config/platform` | Roles: admin

## O que é

A aba de Plataforma permite que administradores visualizem e editem os parâmetros de configuração da plataforma por namespace. Usa o Config API como backend — todos os parâmetros têm dois níveis: default global e override por tenant.

## Funcionamento

O Config API armazena configurações em PostgreSQL com cache Redis (TTL 60 s). Cada entrada tem:
- `namespace` — grupo lógico (ex: `routing`, `sentiment`, `masking`)
- `key` — identificador do parâmetro
- `value` — valor JSONB (string, número, array, objeto)
- `tenant_id` — `'__global__'` para defaults globais; tenant real para overrides

**Lookup**: tenant override > global default.

## Namespaces disponíveis

| Namespace | Descrição | Consumidor |
|---|---|---|
| `sentiment` | Thresholds de classificação, TTL de dados live | analytics-api, AI Gateway |
| `routing` | Snapshot TTL, SLA default, score weights, `performance_score_weight` | routing-engine |
| `session` | TTL da sessão Redis, TTL do Channel Gateway | Core, Channel Gateway |
| `consumer` | batch_size, timeout_ms, restart_delay_s do Kafka consumer | analytics-api consumer |
| `dashboard` | sse_interval_s, sse_retry_ms | analytics-api SSE |
| `webchat` | auth_timeout_s, attachment_expiry_days, upload_limits_mb, JWT secret por tenant | channel-gateway |
| `masking` | authorized_roles (que podem ver original_content), retenção, capture_input/output | mcp-server-plughub |
| `quota` | max_concurrent_sessions, llm_tokens_daily, messages_daily | orchestrator-bridge, Core |
| `pricing` | unit_prices por recurso, reserve_markup_pct, billing_cycle_day, currency | pricing-api |
| `ai_gateway` | Rotação multi-conta, throttle TTL, evaluation_model, fallback OpenAI | ai-gateway |
| `routing` | performance_score_weight | routing-engine |
| `agent_activity` | pause_reasons (motivos de pausa de agentes humanos) | platform-ui, orchestrator-bridge |
| `evaluation` | workflow_context_ttl_s, default_review_skill_id, prazos de revisão/contestação | evaluation-api |
| `dashboards` | default_template_id, allow_user_customization, max_cards_per_dashboard | platform-ui |

## NamespaceEditor

Componente principal da página. Funcionalidades:

- **Sidebar de namespaces**: lista todos os namespaces; clicar carrega as keys
- **Tabela de entries**: key, valor resolvido, badge "tenant override" quando `tenant_id ≠ '__global__'`
- **Scope selector** no edit mode: 🌐 Global default vs 🏢 Tenant override
- **Reset button**: deleta o override tenant e restaura o default global; só aparece quando admin token está configurado
- **Description display**: descrição por key vinda do Config API
- **JSON editor inline**: edição direta do valor JSON com validação

## config.changed — propagação de mudanças

O Config API publica `config.changed` no Kafka após cada PUT/DELETE. Consumidores roteiam por namespace:

| Namespace | Consumidor | Reação |
|---|---|---|
| `quota` | orchestrator-bridge | `bootstrap.request_refresh()` — reconcilia instâncias |
| `routing` | routing-engine | Invalida `RoutingConfigCache`, recarrega `performance_score_weight` |
| Outros | (cache Redis 60 s) | Propagação natural via TTL |

## APIs envolvidas

| Endpoint | Descrição |
|---|---|
| `GET /config/{namespace}` | Lista todas as keys do namespace (resolve tenant + global) |
| `GET /config/{namespace}/{key}` | Valor de uma key específica |
| `PUT /config/{namespace}/{key}` | Upsert valor (com scope: global ou tenant) |
| `DELETE /config/{namespace}/{key}?tenant_id=` | Remove override ou default |

Auth admin: `X-Admin-Token` header ou `?admin_token=` query param (fallback para DELETE via nginx).

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `config-api` | CRUD REST, cache Redis, `config.changed` Kafka (porta 3600) |
| `platform-ui` | `modules/config-plataforma/components/NamespaceEditor.tsx` |

## Referências

- Backend: `packages/config-api/`
- Frontend: `packages/platform-ui/src/modules/config-plataforma/`
- Seed: `packages/config-api/src/plughub_config_api/seed.py`
