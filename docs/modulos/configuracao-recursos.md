# Módulo: Configuração → Recursos

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/config/resources` | Roles: admin, developer

## O que é

A aba de Recursos é onde administradores gerenciam os artefatos estruturais da plataforma: pools de atendimento, tipos de agente, skills (flows YAML), instâncias em execução, canais de comunicação e perfis de agentes humanos.

## Abas internas

| Aba | Arquivo | Descrição |
|---|---|---|
| **Pools** | `PoolsPage.tsx` | CRUD de pools: channel_types, SLA target, descrição, mentionable_pools, hooks |
| **Agent Types** | `AgentTypesPage.tsx` | CRUD de agent_types: framework, role, max_concurrent_sessions, pools vinculados, skills |
| **Skills** | `SkillsPage.tsx` | Lista de skills YAML gerenciadas (read-mostly); detail modal com tools e knowledge_domains |
| **Instances** | `InstancesPage.tsx` | Lista read-only de instâncias ativas com auto-refresh 15 s; filtro dinâmico por pool e status |
| **Canais** | `ChannelsPage.tsx` | CRUD de GatewayConfig (credenciais e settings por tipo de canal) |
| **Agentes Humanos** | `HumanAgentsPage.tsx` | Status live de instâncias humanas + CRUD de agent_types framework=human |

## Pools

| Campo | Tipo | Descrição |
|---|---|---|
| `pool_id` | string | Identificador único (snake_case, sem versão) |
| `channel_types` | string[] | Filtro hard de roteamento: whatsapp, webchat, voice, email, sms, instagram, telegram, webrtc |
| `sla_target_ms` | int | Meta de SLA em millisegundos |
| `hooks.on_human_start` | PoolHookEntry[] | Agentes ativados quando humano entra na sessão |
| `hooks.on_human_end` | PoolHookEntry[] | Agentes ativados quando último humano encerra |
| `hooks.post_human` | PoolHookEntry[] | Agentes ativados após on_human_end concluir |
| `mentionable_pools` | object | Aliases de @mention → pool_id de especialistas |
| `mentionable_journeys` | object | Aliases de `@journey:<skill_id>` → tipos de Journey iniciáveis pelo pool (Arc 16); alimenta o botão "Iniciar Processo" no Console |
| `webrtc_media_fallback_order` | string[] | Ordem de fallback de medium na negociação WebRTC: ex. `[video, voice, text]` (Arc 15) |
| `inbound_journey_resume` | boolean | Flag informacional para o skill author — o agente do pool deve oferecer retomada de Journey pendente no inbound (Arc 16) |

## Agent Types

| Framework | Descrição |
|---|---|
| `plughub-native` | Agente IA nativo executado pelo SDK |
| `human` | Agente humano (login via Agent Assist UI) |
| `external-mcp` | Agente externo via proxy sidecar |
| `langgraph`, `crewai`, `anthropic_sdk`, `azure_ai`, `google_vertex` | Frameworks externos suportados |
| `generic_mcp` | MCP client genérico |

**Deprecate flow** (Agent Types): Deprecate → Confirmar → DELETE (soft-delete — não destrói instâncias ativas).

### Campos relevantes do Agent Type

| Campo | Tipo | Descrição |
|---|---|---|
| `framework` | enum | Runtime do agente (ver tabela acima) |
| `role` | enum | `primary` / `specialist` / `supervisor` |
| `max_concurrent_sessions` | int | Capacidade simultânea |
| `media_capabilities` | string[] | Mídias suportadas: `[video, voice, text]` (Arc 15). A ordem implica preferência; vazio = text-only. Usado pela negociação de medium no canal WebRTC |

## Skills

Skills são gerenciadas por arquivo YAML em `packages/skill-flow-engine/skills/` e sincronizadas ao banco via RegistrySyncer no startup da bridge. O painel Skills é **read-mostly** — não permite criar skills diretamente (use o AgentFlow Editor ou edite o YAML e reinicie a bridge). Exibe detail modal com:
- `tools`: ferramentas MCP referenciadas no flow
- `knowledge_domains`: domínios de conhecimento utilizados
- `description` e `classification` (orchestrator/vertical/horizontal)
- `deploy_status` (draft/published)

## Canais (GatewayConfig)

Gestão de credenciais por tipo de canal:

| Canal | Credenciais (mascaradas em leitura) | Settings |
|---|---|---|
| WhatsApp | access_token, phone_number_id, waba_id, webhook_verify_token | api_version, webhook_path |
| Webchat | jwt_secret | ws_auth_timeout_s, attachment_expiry_days, serving_base_url |
| Voice | api_key, api_secret, account_sid | inbound_number, provider, region |
| Email | smtp_password, api_key | smtp_host, smtp_port, from_address |
| SMS | api_key, api_secret | sender_id, provider |
| Instagram | access_token, app_secret, webhook_verify_token | page_id, api_version |
| Telegram | bot_token | webhook_path, bot_username |
| WebRTC | turn_secret | stun_url, turn_url, turn_username |

Credenciais são **sempre mascaradas** em leitura (`••••••`). Atualizar: preencha apenas os campos com novo valor. Triggers `registry.changed` em create/update/delete.

## Agentes Humanos (HumanAgentsPage)

**Live Status tab**: tabela de instâncias `framework=human` com filtro por status (Ready/Busy/Paused). Ações por status:
- Ready → Pausar
- Busy → Pausar, Force Logout
- Paused → Retomar, Force Logout

Polling automático a cada 10 s. PATCH `/v1/instances/:id` com `{ action: pause | resume | force_logout }`.

**Profiles tab**: CRUD de `AgentType` com `framework=human`. Campos: agent_type_id, role (primary/specialist/supervisor), max_concurrent_sessions, pools associados. **Deprecate flow** igual ao Agent Types.

## APIs envolvidas

| Endpoint | Descrição |
|---|---|
| `GET/POST /v1/pools` | Lista e cria pools |
| `GET/PUT/DELETE /v1/pools/:id` | Detalhe, atualiza e remove pool |
| `GET/POST /v1/agent-types` | Lista e cria agent_types |
| `GET/PUT/DELETE /v1/agent-types/:id` | Detalhe, atualiza e depreca |
| `GET /v1/skills` | Lista skills (com filtro) |
| `GET/DELETE /v1/skills/:id` | Detalhe e remove skill |
| `GET /v1/instances` | Lista instâncias (filtros: pool, status, framework) |
| `PATCH /v1/instances/:id` | Ação de operador (pause/resume/force_logout) |
| `GET/POST /v1/channels` | Lista e cria GatewayConfig |
| `GET/PUT/DELETE /v1/channels/:id` | Detalhe, atualiza e remove canal |

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `agent-registry` | API REST de pools, agent_types, skills, instances, channels (porta 3300) |
| `orchestrator-bridge` | Reconcilia instâncias Redis a partir do agent-registry; processa `registry.changed` |
| `platform-ui` | `modules/config-recursos/` — 6 abas |

## Referências

- Backend: `packages/agent-registry/src/routes/`
- Frontend: `packages/platform-ui/src/modules/config-recursos/`
- Schemas: `packages/schemas/src/agent-registry.ts`
