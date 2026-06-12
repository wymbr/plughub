# Config Consolidation — inventário de fontes de configuração da plataforma

> Meta (decisão do usuário, 2026-06-11): a configuração da plataforma deve ter **uma fonte única por
> domínio**, editável pela UI, **sem seed fora da config** (sem escrita direta em Redis/DB, sem
> duplicação hardcoded). Env vars de segurança são exceção a mapear depois. Este doc inventaria TODAS
> as fontes de config hoje, para depois agrupar/consolidar. Detalhe de campos de pool:
> `pool-config-surface.md`.

## 1. Resposta direta: além do `tenant_demo.yaml`, há outras fontes?

**Sim.** A config hoje está espalhada em **arquivos config-as-code** + **scripts de seed** + os **defaults
hardcoded** de cada serviço.

### Arquivos config-as-code

| Arquivo | Configura | Store-alvo | Consumidor |
|---|---|---|---|
| `infra/registry/tenant_demo.yaml` | pools, (skills, agent_types) | agent-registry | RegistrySyncer (orchestrator-bridge) |
| `packages/skill-flow-engine/skills/*.yaml` (26) | flows (skills) | agent-registry (skills) | RegistrySyncer (mount) |
| `infra/modules.yaml` | registro de módulos ABAC | auth-api | auth-api (startup) |
| `infra/config-seed/masking-context-rules.json` | regras de mascaramento por contexto | (arquivo) | mcp-server `lib/masking.ts` |

### Scripts de seed (`infra/seed/*.py`)

| Script | Provisiona | Store-alvo | ⚠ |
|---|---|---|---|
| `seed.py` | pools + agent_types **(lista HARDCODED — duplica o YAML)** + **Redis direto** (pool_configs, global pools set, rosters) | agent-registry **+ Redis** | dupla fonte de pool + escrita direta em Redis |
| `seed_pricing.py` | recursos contratados / plano | pricing-api | — |
| `seed_auth.py` | usuários demo + `module_config` (ABAC) | auth-api | — |
| `seed_evaluation.py` | formulário + campanha de avaliação | evaluation-api | — |

### Seed do próprio config-api

| Fonte | Provisiona | Store-alvo |
|---|---|---|
| `config-api/seed.py` `_SEED` (hardcoded) | 12 namespaces de **defaults globais** | config-api `platform_config` |

## 2. O que já está agrupado em "Configurations" (config-api)

`config-api` (`platform_config`, KV por namespace) já centraliza **12 namespaces horizontais**:
`sentiment`, `routing`, `session` (TTLs), `analytics_consumer`, `dashboard(s)`, `webchat`,
`audit_policy` (masking), `pricing` (preços/markup), `ai_gateway` (rotação/isolamento), `agent_activity`
(pause_reasons, escalation_reasons), `evaluation` (defaults). São **settings de plataforma**, não entidades.

## 3. Stores de config em runtime (onde o dado vive)

| Store | Domínio (entidades/settings) | Tem UI de edição? |
|---|---|---|
| **config-api** `platform_config` | 12 namespaces de settings horizontais | parcial (Platform/Masking/Billing) |
| **agent-registry** | pools, skills, agent_types | parcial (resources/pool — subconjunto; ver pool-config-surface) |
| **auth-api** | usuários, `module_registry` (ABAC), agent_groups | parcial (Access, Groups) |
| **evaluation-api** | forms, campaigns, knowledge | sim (Avaliação) |
| **pricing-api** | resources/planos contratados | sim (Billing) |
| **Redis** | pool_configs, rosters (escritos por seed.py) | não — estado, não deveria ser "seed" |

## 4. Problemas que violam "uma fonte única, sem seed fora da config"

1. **Pool com fonte dupla**: `tenant_demo.yaml` (→RegistrySyncer) **e** `seed.py` (lista hardcoded →
   agent-registry HTTP **+ Redis direto**). Risco de drift; a escrita direta em Redis é seed fora da config.
2. **Campos de pool só no YAML**: hooks (wrap-up/NPS), supervisor_config/escalation_pools,
   mentionable_pools, deploy, evaluation, etc. não são editáveis na UI (`pool-config-surface.md`).
3. **Config espalhada por 5 stores + Redis**, alimentada por **8 fontes** (4 arquivos + 4 scripts) +
   defaults hardcoded em cada serviço.
4. **Defaults hardcoded** nos serviços (o próprio header do config-api seed diz "all values currently
   hardcoded across packages") — alguns ainda não migrados para o config-api.

## 5. Princípio de consolidação (proposto)

Não significa "tudo dentro do config-api KV" — entidades ricas (pools, users, forms, planos) seguem nos
seus stores de domínio. Significa, por store:
1. **Fonte única**: cada store é a única fonte do seu domínio (eliminar duplicação — ex.: pool no YAML
   *ou* no seed.py, nunca os dois).
2. **Editável na UI**: todo campo de config tem superfície na tela (fechar o gap de pool primeiro).
3. **Provisão só pela API oficial**: bootstrap idempotente que escreve **através da API** do store —
   **sem escrita direta em Redis/DB, sem listas hardcoded**. O "seed" do demo vira uma orquestração que
   chama as APIs (registry/auth/eval/pricing/config), nunca bypassa.
4. **Env vars**: mapear as de segurança (secrets, URLs) num inventário à parte; ficam fora da config por design.

## 6. Inventário de env vars (compose) — o que fica e o que vazou

Três categorias. **A** e **B** ficam em env por design (a exceção de segurança/topologia que você citou);
**C** é config de negócio que **vazou** pra env e deveria estar no config-api.

### A. Secrets / credenciais (ficam em env — segurança)
JWTs: `JWT_SECRET`, `PLUGHUB_AUTH_JWT_SECRET`, `PLUGHUB_EVALUATION_JWT_SECRET`, `PLUGHUB_JWT_SECRET`
(webchat), `PLUGHUB_ADMIN_JWT_SECRET`. Admin tokens: `*_ADMIN_TOKEN` (auth/config/pricing/eval/knowledge).
Creds de infra: `POSTGRES_PASSWORD`, `CLICKHOUSE_PASSWORD`, `MINIO_ROOT_*`, `PLUGHUB_S3_*_KEY`,
`DATABASE_URL`s. Externos: `PLUGHUB_ANTHROPIC_API_KEY`. Seed admin: `PLUGHUB_AUTH_SEED_ADMIN_*`.

### B. Wiring / topologia (ficam em env — deployment)
Service discovery (`*_URL`), `KAFKA_BROKERS`, `REDIS_URL`, `CLICKHOUSE_HOST/PORT`, `PORT`, nomes de
tópico Kafka, `SKILLS_DIR`/`REGISTRY_CONFIG_DIR`, `*_TENANT_ID`/`BOOTSTRAP_TENANT_IDS` (escopo de deploy).

### C. Config de negócio que VAZOU pra env (candidatos a mover p/ config-api)
| Env var | Serviço | Deveria viver em | Obs |
|---|---|---|---|
| `PLUGHUB_INSTANCE_TTL_SECONDS` (3600) | orchestrator-bridge | config-api `session` | TTLs já moram no ns `session` — **duplicado** |
| `PLUGHUB_ATTACHMENT_EXPIRY_DAYS` (7) | channel-gateway | config-api `webchat` | ns `webchat` já existe — **duplicado** |
| `PLUGHUB_WS_AUTH_TIMEOUT_S` (30) | channel-gateway | config-api `webchat` | ✅ F2-TTL (config-api vence; webchat+webrtc via `resolve_ws_auth_timeout_s`; env removido; guard cobre) |
| `REPLAY_SPEED_FACTOR` (10) | session-replayer | config-api (replay/eval) | ✅ item 7b → `evaluation.replay_speed_factor` |
| `EVALUATOR_POOL` (avaliacao_ia) | session-replayer | config-api `evaluation` ou registry | ✅ item 7b → `evaluation.evaluator_pool` (default errado `avaliador_qualidade` corrigido) |
| `VITE_DEFAULT_POOL` (retencao_humano) | platform-ui | config-api (ui) | ✅ item 7a — era **env morto** (não lido); removido do compose (era do agent-assist-ui) |
| `PLUGHUB_ANALYTICS_OPEN_ACCESS` (true) | analytics-api | env (flag de demo) | OK ficar (mas perigoso em prod) |

**Achado**: TTLs e attachment-expiry estão **em env E no config-api** ao mesmo tempo — risco de divergência
(qual vence?). Auditar a precedência (env vs config) é parte da consolidação.

## 7. Gap por store (alvo: fonte única + UI + provisão só via API)

| Store | Fontes hoje | Editável na UI? | Gap principal |
|---|---|---|---|
| config-api | seed `_SEED` (hardcoded) + masking JSON | parcial | defaults ainda hardcoded em serviços; env vars cat. C |
| agent-registry | `tenant_demo.yaml` **+** `seed.py` (dup + Redis) | parcial | **fonte dupla**; campos de pool fora da UI (pool-config-surface) |
| auth-api | `modules.yaml` + `seed_auth.py` | parcial | módulos ABAC e users semeados por 2 caminhos |
| evaluation-api | `seed_evaluation.py` | sim | seed via script (poderia ser bootstrap idempotente via API) |
| pricing-api | `seed_pricing.py` | sim (Billing) | idem |
| Redis | `seed.py` direto | não | **estado não deveria ser semeado** — deriva do boot normal |

## 8. Plano de execução — estratégia HÍBRIDA (decidida 2026-06-11)

Espinha: **contrato+guard primeiro → triar perigos ativos → migrar por domínio (read-path-first),
limpando cada módulo oportunisticamente**. Cada fase é validável (o usuário roda build/queries).

### Fase 0 — Contrato + guard-rail (fundação, impede sangria nova)
- **0.1** Invariantes de config no CLAUDE.md: (i) **fonte única por domínio**; (ii) **provisão só via
  API oficial** do store — proibido escrita direta em Redis/DB e listas hardcoded; (iii) **todo campo
  editável na UI**; (iv) **env só para secret/wiring** (cat. A/B do §6); config de negócio nunca em env.
- **0.2** Guard objetivo: arquivo-checklist das violações conhecidas (deste doc) + teste que **falha se
  a lista crescer** (burn-down). Lints específicos p/ os piores: (a) seed que escreve Redis/DB direto;
  (b) env que duplica chave já existente no config-api.

### Fase 1 — Triagem dos perigos ativos (drift que já existe hoje)
- **1.1** De-duplicar pools: `infra/seed/seed.py` deixa de ter lista hardcoded + escrita Redis; a
  agent-registry (YAML→RegistrySyncer/API) vira **fonte única**. O que o seed.py escreve em Redis hoje
  precisa vir do boot normal (Bootstrap/reconcile), não de seed. Validar boot sem o seed de pools.
- **1.2** Precedência env×config: decidir **config-api vence**; remover (ou fazer o serviço ler do
  config-api) `PLUGHUB_INSTANCE_TTL_SECONDS`, `PLUGHUB_ATTACHMENT_EXPIRY_DAYS` e afins duplicados.

### Fase 2 — Migração por domínio (read-path-first) + limpeza por módulo
Para cada domínio: (a) canonizar no store; (b) readers apontam pro store **com fallback**; (c) deletar
fontes duplicadas/seed; (d) expor na UI. Ordem sugerida (maior risco/valor primeiro):
1. **Pools** — fechar o gap de campos na UI (`pool-config-surface.md` Fase 2): hooks (NPS/wrap-up),
   escalation_pools, mentionable_pools, deploy, evaluation, etc.
2. **TTLs/timeouts** (ns `session`/`webchat`) — matar duplicação env×config.
3. **Hooks** (NPS/wrap-up/post) — parte de pools, mas é a config que o usuário mais citou.
4. **Masking** (`audit_policy`) — unificar `masking-context-rules.json` no config-api.
5. **ABAC/users** (`modules.yaml` + `seed_auth`) — fonte única no auth-api via API.
6. **Evaluation / Pricing** — seeds viram bootstrap idempotente via API.
7. **Defaults hardcoded restantes** nos serviços.

### Fase 3 — Bootstrap idempotente único (substitui os seeds dispersos)
Uma orquestração de provisionamento que lê uma fonte declarativa **única** e escreve **só via APIs**
(registry/auth/eval/pricing/config) — aposenta `infra/seed/*.py`, a escrita Redis e o YAML-como-fonte-
separada. Liga à "Fase 3 — Config + Deploy" do CLAUDE.md.

### Fase 4 — Política de env vars (segurança)
Inventário final (cat. A/B do §6) + política: o que fica em env por design e por quê.

> **Limpeza oportunista (a proposta "por módulo" do usuário)**: ao passar por cada módulo numa fase
> acima, eliminar de uma vez qualquer config indevida local dele — sem virar uma varredura própria.
