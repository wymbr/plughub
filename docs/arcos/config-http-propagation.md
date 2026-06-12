# Config HTTP Propagation — consumo confiável de config fora do config-api

> Arco aberto em 2026-06-12 (durante a Fase 2 da config-consolidation, item "masking").
> Corrige uma classe de bug: consumidores que liam config **direto do Redis** liam uma chave
> que **nunca é escrita** → sempre caíam no default do código. "config-api vence" não acontecia.

## Diagnóstico

O config-api mantém no Redis apenas a **cache TTL** `plughub:cfg:{tenant}:{ns}:{key}` (60s),
**invalidada** (deletada) em cada escrita e populada preguiçosamente só após uma leitura via a
**API HTTP** (`store.get` → `cache.set`). Não existe representação durável.

Apesar disso, vários consumidores liam config **direto do Redis**:

| Consumidor | Chave lida | Quem escreve? | Efeito real |
|---|---|---|---|
| channel-gateway `resolve_attachment_expiry_days` (F1.2) | `{tenant}:config:webchat:attachment_expiry_days` | **ninguém** | sempre default 30 |
| channel-gateway `resolve_ws_auth_timeout_s` (F2-TTL) | `{tenant}:config:webchat:auth_timeout_s` | **ninguém** | sempre default 30 |
| mcp-server `masking.ts::loadContextMaskingConfig` | `plughub:cfg:...:masking:context_rules` | cache TTL (transitória) | quase sempre MISS → `DEFAULT_CONTEXT_MASKING_CONFIG` |

As chaves `{tenant}:config:...` (formato F1.2/F2-TTL) **não são escritas por ninguém** (o config-api
escreve `plughub:cfg:...`, não `{tenant}:config:...`; o channel-gateway não consome `config.changed`).
A cache `plughub:cfg:...` que o masking lê é TTL e some 60s após a última leitura via API.

## Padrão de referência (o que JÁ funciona)

`orchestrator-bridge/session_config.py::SessionConfigCache` e
`routing-engine/routing_config.py::RoutingConfigCache`: buscam via **HTTP**
(`GET /config/{ns}?tenant_id=`), cacheiam **in-process**, invalidam no **`config.changed`** Kafka
(filtrando por `namespace`), recarregam em background, e têm **defaults no código** espelhando o seed.

## Alvo

Todo consumidor externo de config segue esse padrão. **Zero leitura direta** da cache Redis do
config-api. Default no código continua como fallback (config-api indisponível).

## Fases

- **Fase 1 ✅ (2026-06-12) — channel-gateway** (conserta F1.2 + F2-TTL). `WebchatConfigCache`
  (`webchat_config.py`) espelha o `SessionConfigCache`: `GET /config/webchat?tenant_id=`, cache
  in-process, invalidação no `config.changed` (ns `webchat`) via novo consumer no `main.py`, reload no
  startup. `resolve_attachment_expiry_days`/`resolve_ws_auth_timeout_s` passam a ler do cache (não do
  Redis); assinaturas mantidas por compat dos call-sites. Setting `config_api_url` + env
  `PLUGHUB_CONFIG_API_URL`. Testes reescritos (cache, não Redis). Ver CHANGELOG.
- **Fase 2 ✅ (2026-06-12) — mcp-server masking.** `loadContextMaskingConfig` passou a buscar
  `context_rules` via `GET /config/masking?tenant_id=` (HTTP) com validação `ContextMaskingConfigSchema` →
  fallback `DEFAULT_CONTEXT_MASKING_CONFIG`; o cache TTL 60s in-process do `server.ts` foi mantido.
  Removidas as leituras diretas de `plughub:cfg:...` e o `saveContextMaskingConfig` dead-code. Seed
  `masking.context_rules` global no config-api (= conteúdo do JSON órfão). `CONFIG_API_URL` no
  mcp-server. JSON órfão `infra/config-seed/masking-context-rules.json` aposentado (git rm). Ver CHANGELOG.
  *Pendente derivado*: `authorized_roles` (stream masking) ainda lê do Redis, mas tem caminho durável
  legado `{tenant}:masking:access_policy` que funciona — migrar na Fase 3 por consistência.
- **Fase 3 (pendente) — varredura + guard.** Achar outros leitores diretos de `:config:`/`plughub:cfg:`
  fora do config-api (ex.: credenciais `{tenant}:config:sms|whatsapp:...` — caminho de GatewayConfig, a
  avaliar) e migrar/registrar. Opcional: lint no guard que falhe em leitura direta dessas chaves fora do
  config-api (impede regressão do padrão furado).

## Invariante (proposto)

> Config de plataforma é consumida **só via a API HTTP do config-api** (com cache in-process +
> invalidação `config.changed`). Nenhum serviço lê as chaves Redis do config-api
> (`plughub:cfg:...` ou `{tenant}:config:...`) diretamente.
