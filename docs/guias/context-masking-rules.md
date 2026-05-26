# ContextStore — Mascaramento Dinâmico por Variável × Role

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Status:** Fases A, B e C implementadas — Fase D (monitoring) deferred  
> **Dependências:** `docs/guias/context-store-taxonomy.md`, `packages/schemas/src/audit.ts`, Config API (port 3600)  
> **Motivação:** o `TAG_PII_CATEGORY` implementado na Fase 1 do mascaramento é hardcoded — toda nova variável PII no ContextStore exige alteração de código. Este documento especifica o mecanismo dinâmico que o substitui: regras configuráveis por variável × role, vivas no Config API e editáveis pela UI de mascaramento.

---

## Distinção fundamental: dois sistemas de mascaramento

O PlugHub possui dois mecanismos de mascaramento distintos que **não devem ser confundidos**:

| Sistema | Propósito | Onde age | Mecanismo |
|---|---|---|---|
| **Stream masking** (`MaskingRule`) | Detecta e mascara PII em texto livre de mensagens | Core, Channel Gateway, canal de voz | Regex sobre texto arbitrário |
| **Context field masking** (`ContextMaskingRule`) | Controla exibição de variáveis nomeadas do ContextStore | `supervisor_state` REST endpoint, ContextoTab | Lookup por nome de variável |

O `MaskingRule` existente (em `schemas/audit.ts`) e os `DEFAULT_MASKING_RULES` **permanecem intactos** para o stream masking. Este documento define exclusivamente o `ContextMaskingRule`, um tipo novo para o segundo sistema.

---

## Problema

O `TAG_PII_CATEGORY` implementado em `server.ts` é um mapa hardcoded:

```typescript
const TAG_PII_CATEGORY: Record<string, string> = {
  "caller.cpf":              "cpf",
  "caller.telefone":         "phone",
  // ...
}
```

Cada vez que um agente começa a escrever uma nova variável sensível no ContextStore (ex: `caller.rg`, `caller.passaporte`, `account.score_credito`), é necessário:

1. Alterar código em `server.ts`
2. Fazer deploy do mcp-server
3. A tag fica exposta até o deploy

Além disso, a granularidade atual é por namespace (operator vê/não vê `caller.*` inteiro), não por campo — o que é impreciso para casos onde `caller.nome` pode ser exibido mas `caller.cpf` não.

---

## Modelo de Dados — ContextMaskingRule

### Tipos de mascaramento

O `MaskingType` é uma enumeração de **apresentações visuais** — desvinculado do tipo semântico do dado (cpf, telefone, etc.). O administrador decide qual apresentação aplicar a cada variável.

| `MaskingType` | Resultado visual | Caso de uso típico |
|---|---|---|
| `plain` | Valor original completo | Dados não-sensíveis; supervisor vendo tudo |
| `hidden` | Campo removido da resposta | Dados que operator nunca deve ver (ex: limite de crédito) |
| `full` | `***` | Valor existe mas conteúdo completamente ocultado |
| `last_2` | `***XX` | CPF (2 últimos dígitos visíveis) |
| `last_4` | `***XXXX` | Cartão, contrato, telefone (4 últimos dígitos) |
| `first_1` | `X***` | Quando só o inicial importa |
| `first_word` | `Palavra ***` | Nome — só o primeiro nome visível |
| `email_domain` | `X***@domain.com` | E-mail — inicial + domínio preservados |
| `financial` | `R$ ****,**` | Valores monetários (fatura, limite, saldo) |

> **Decisão de design**: os tipos são predefinidos e não configuráveis em template. Isso simplifica a UI e evita erros de configuração. Novos tipos podem ser adicionados ao enum sem quebrar configurações existentes.

### Schema Zod

```typescript
// Em packages/schemas/src/audit.ts — adicionado ao arquivo existente

export const ContextMaskingTypeSchema = z.enum([
  "plain",
  "hidden",
  "full",
  "last_2",
  "last_4",
  "first_1",
  "first_word",
  "email_domain",
  "financial",
])
export type ContextMaskingType = z.infer<typeof ContextMaskingTypeSchema>

/**
 * ContextMaskingRule — regra de mascaramento para uma variável do ContextStore.
 *
 * `pattern` suporta glob simples:
 *   - "caller.cpf"      → match exato
 *   - "caller.*"        → todos os campos do namespace caller
 *   - "session.copilot.*" → todos os campos do sub-namespace copilot
 *   - "*"               → catch-all (qualquer variável)
 *
 * `role` define para qual perfil a regra se aplica:
 *   - "operator"        → atendentes (role mais restritivo)
 *   - "supervisor"      → supervisor, admin, evaluator, reviewer
 *   - "*"               → todos os perfis
 */
export const ContextMaskingRuleSchema = z.object({
  pattern:  z.string().min(1),
  role:     z.union([
    z.literal("operator"),
    z.literal("supervisor"),
    z.literal("*"),
  ]),
  type:     ContextMaskingTypeSchema,
  /** Descrição opcional para exibição na UI de administração. */
  label?:   z.string().optional(),
})
export type ContextMaskingRule = z.infer<typeof ContextMaskingRuleSchema>

/**
 * ContextMaskingConfig — configuração completa armazenada no Config API.
 * Namespace: "masking", key: "context_rules"
 *
 * `default_unmatched_operator`: comportamento quando nenhuma regra cobre
 *  a variável para o role operator. Default conservador: "hidden".
 *  Mude para "plain" apenas em ambientes onde não há dados PII no ContextStore.
 */
export const ContextMaskingConfigSchema = z.object({
  rules: z.array(ContextMaskingRuleSchema).default([]),
  default_unmatched_operator: ContextMaskingTypeSchema.default("hidden"),
})
export type ContextMaskingConfig = z.infer<typeof ContextMaskingConfigSchema>
```

---

## Resolução de Regras

### Algoritmo de matching

Para um par `(tag, caller_role)`, o sistema encontra a regra aplicável seguindo esta ordem de prioridade:

```
1. Exact pattern + exact role   →  "caller.cpf" × "operator"
2. Exact pattern + wildcard role →  "caller.cpf" × "*"
3. Glob pattern + exact role    →  "caller.*" × "operator"
4. Glob pattern + wildcard role  →  "caller.*" × "*"
5. Catch-all + exact role        →  "*" × "operator"
6. Catch-all + wildcard role     →  "*" × "*"
7. Sem match → default_unmatched_operator (operator) ou "plain" (supervisor+)
```

**Regra de ouro**: especificidade vence. Um match exato sempre prevalece sobre um glob; um role específico sempre prevalece sobre `*`.

### Implementação (pseudocódigo)

```typescript
function resolveContextMaskingRule(
  tag:    string,
  role:   string,
  config: ContextMaskingConfig
): ContextMaskingType {
  const isSupervisor = ["supervisor", "admin", "evaluator", "reviewer"].includes(role)

  // Supervisor default: plain (a menos que uma regra explícita diga diferente)
  const fallback = isSupervisor ? "plain" : config.default_unmatched_operator

  // Cria lista de candidatos em ordem de prioridade decrescente
  const candidates = config.rules
    .filter(r => matchPattern(r.pattern, tag) && matchRole(r.role, role))
    .sort((a, b) => specificity(b) - specificity(a))

  return candidates[0]?.type ?? fallback
}

function matchPattern(pattern: string, tag: string): boolean {
  if (pattern === "*") return true
  if (!pattern.includes("*")) return pattern === tag
  // Glob: "caller.*" → ^caller\..+$
  const regex = new RegExp("^" + pattern.replace(".", "\\.").replace("*", ".+") + "$")
  return regex.test(tag)
}

function matchRole(ruleRole: string, callerRole: string): boolean {
  if (ruleRole === "*") return true
  if (ruleRole === "supervisor") {
    return ["supervisor", "admin", "evaluator", "reviewer"].includes(callerRole)
  }
  return ruleRole === callerRole
}

function specificity(rule: ContextMaskingRule): number {
  // pattern: exact=2, glob=1, *=0
  const p = rule.pattern === "*" ? 0 : rule.pattern.includes("*") ? 1 : 2
  // role: exact=2, "supervisor"=1, *=0
  const r = rule.role === "*" ? 0 : rule.role === "supervisor" ? 1 : 2
  return p * 10 + r
}
```

---

## Armazenamento — Config API

### Chave Redis e namespace

```
Config API namespace:  "masking"
Config API key:        "context_rules"
Redis key (cacheado):  {tenantId}:config:masking:context_rules
```

O Config API já persiste `{tenant_id}:config:{namespace}:{key}` no Redis com invalidação via evento Kafka `config.changed`. O `context_rules` se encaixa neste padrão sem alterações na infraestrutura.

### Seed global (defaults de plataforma)

As regras abaixo substituem o `TAG_PII_CATEGORY` hardcoded e são entregues como seed global (tenant_id = `"__global__"`). Tenants podem criar overrides por chave.

```json
{
  "rules": [
    { "pattern": "caller.cpf",              "role": "operator", "type": "last_2",       "label": "CPF — últimos 2 dígitos" },
    { "pattern": "caller.cnpj",             "role": "operator", "type": "last_2",       "label": "CNPJ — últimos 2 dígitos" },
    { "pattern": "caller.telefone",         "role": "operator", "type": "last_4",       "label": "Telefone — últimos 4 dígitos" },
    { "pattern": "caller.email",            "role": "operator", "type": "email_domain", "label": "E-mail — inicial + domínio" },
    { "pattern": "account.numero_contrato", "role": "operator", "type": "last_4",       "label": "Contrato — últimos 4 dígitos" },
    { "pattern": "account.valor_fatura",    "role": "operator", "type": "financial",    "label": "Fatura — valor ocultado" },
    { "pattern": "account.limite_credito",  "role": "operator", "type": "hidden",       "label": "Limite de crédito — oculto para operator" }
  ],
  "default_unmatched_operator": "plain"
}
```

> **Nota sobre `default_unmatched_operator: "plain"`**: o default é permissivo porque a maioria das tags no ContextStore (`service.*`, `journey.*`, `session.*`) não são PII. O seed cobre explicitamente todas as tags PII conhecidas com o tipo correto. Novas tags PII não cobertas ficam visíveis — esse é o sinal para o administrador adicionar uma regra.  
> Para deployments com política mais restritiva, o administrador pode mudar para `"hidden"` — qualquer tag sem regra ficará oculta para operator até ser explicitamente liberada.

---

## Relação com `context_visibility.operator_namespaces`

Os dois mecanismos coexistem e operam em camadas diferentes:

```
Camada 1: context_visibility.operator_namespaces (pool config)
  → gate grosso de namespace: "este pool exibe caller.* para operator?"
  → se namespace excluído: nenhum campo daquele namespace chega ao frontend

Camada 2: ContextMaskingRule (tenant config, Config API)
  → gate fino de campo: "este campo específico é exibido como?"
  → aplicado apenas nos campos que passaram pela Camada 1
```

**Exemplo**: pool de SAC com `operator_namespaces: ["service","journey","session"]` — operator nunca vê `caller.*`, independente das regras de mascaramento. Pool de cobrança com `operator_namespaces: ["service","journey","session","account","caller"]` — operator vê os namespaces, mas `caller.cpf` aparece como `***-XX` por força da regra de mascaramento.

---

## Problemas Identificados e Mitigações

### P1 — Default para variáveis novas

**Problema**: agente escreve `caller.passaporte` (variável não prevista). Sem regra explícita, `default_unmatched_operator = "plain"` expõe o dado.

**Mitigação**: 
- O seed global cobre os campos PII mais comuns.
- Monitoramento: o sistema pode logar quando `default_unmatched_operator` é aplicado a um campo do namespace `caller.*` ou `account.*` — isso sinaliza ao admin que uma regra está faltando.
- Em deployments com compliance rígida, mudar o default para `"hidden"` inverte a lógica: novos campos ficam ocultos até serem explicitamente liberados.

### P2 — Sobreposição de regras (conflito)

**Problema**: admin cadastra `"caller.*" → operator → last_4` e também `"caller.cpf" → operator → last_2`. Qual prevalece?

**Mitigação**: a especificidade do algoritmo de matching é determinística — exact sempre vence glob. O match mais específico (`caller.cpf`) prevalece. A UI de administração deve exibir o resultado resolvido como preview ao lado de cada regra.

### P3 — Supervisores e LGPD

**Problema**: `supervisor` vê CPF completo no ContextoTab. Isso deveria ser auditado (LGPD). Hoje não é.

**Mitigação**: deferido para Audit LGPD Fase 3 (`user_access` logs). A spec do LGPD já prevê isso. Enquanto isso, um administrador pode adicionar a regra `"caller.cpf" × "supervisor" → last_2` se quiser mascarar mesmo para supervisor — o mecanismo suporta isso.

### P4 — Performance da resolução de regras

**Problema**: `supervisor_state` é chamado a cada 3s por sessão ativa. Com 50 campos no ContextStore e 20 regras, são 50 × 20 comparações por chamada.

**Mitigação**: as regras são lidas do Redis em uma única operação `GET` (já cacheadas pelo Config API). Compilar o set de regras em um matcher pré-computado por (tenant, role) e invalidar via `config.changed` Kafka. Custo total < 1ms por chamada — aceitável.

### P5 — `evaluator` vs `reviewer` vs `supervisor`

**Problema**: o `evaluator` acessa o ContextoTab durante avaliação de qualidade. Ele realmente precisa ver CPF completo? Talvez o `evaluator` deveria ter uma política diferente do `supervisor`.

**Mitigação**: o schema agrupa `supervisor`, `admin`, `evaluator` e `reviewer` sob o role `"supervisor"` nas regras, por simplicidade. Se um tenant precisar de granularidade maior (ex: `evaluator` vê CPF mascarado), pode adicionar regras específicas para `evaluator` — o schema suporta qualquer string como role. Mas o seed default trata todos como privilegiados. Isso pode ser revisado quando Arc 13 (Evaluation Review) for implementado.

### P6 — Compatibilidade com implementação atual

**Problema**: o `TAG_PII_CATEGORY` hardcoded em `server.ts` foi implementado na Fase 1. A migração para o sistema dinâmico precisa ser feita sem quebrar o comportamento existente.

**Mitigação**: a migração é direta — o seed global contém exatamente as mesmas regras que o `TAG_PII_CATEGORY` cobre. O comportamento para todos os tenants existentes é idêntico. A Fase B do plano de implementação substitui o código hardcoded mantendo o mesmo resultado.

### P7 — `agent.*` namespace

**Problema**: tags `agent.*` têm visibilidade por `participant_id` (só o próprio agente que escreveu). A lógica atual remove `agent.*` do ContextoTab incondicionalmente, antes de aplicar mascaramento.

**Decisão**: manter o tratamento especial de `agent.*` separado das `ContextMaskingRule`. As regras de mascaramento não se aplicam a `agent.*` — esse namespace é filtrado antes do matching de regras. Isso evita que um admin inadvertidamente crie uma regra `"agent.* × operator → plain"` e exponha notas privadas de outros agentes.

---

## Plano de Implementação

### ✅ Fase A — Schema + Seed (concluída 2026-05-17)

**`@plughub/schemas/audit.ts`**
- `ContextMaskingTypeSchema`, `ContextMaskingRuleSchema`, `ContextMaskingConfigSchema`, `DEFAULT_CONTEXT_MASKING_CONFIG` implementados

**`mcp-server-plughub/src/lib/masking.ts`**
- `MaskingService.loadContextMaskingConfig(redis, tenantId)` — lookup chain 3 tiers com `safeParse()`
- `MaskingService.saveContextMaskingConfig(redis, scope, config)` — persiste por tenant ou global

**Seed**
- `infra/config-seed/masking-context-rules.json` criado com as regras default do seed global

### ✅ Fase B — Backend dinâmico (concluída 2026-05-17)

**`mcp-server-plughub/src/server.ts`**
- `TAG_PII_CATEGORY` e `maskPiiValue()` removidos
- `getContextMaskingConfig(redis, tenantId)`: cache TTL 60s por tenant + `MaskingService.loadContextMaskingConfig()` em miss
- `resolveContextMaskingRule(tag, callerRole, config)`: algoritmo de especificidade (exact > glob > `*`; role match +2)
- `applyMaskingTypeToValue(raw, type)`: implementa os 9 tipos visuais
- `applyContextMaskingDynamic(rawHash, role, allowedNs, redis, tenantId)` (async): substitui `applyContextMasking()`
- Handler `GET /api/supervisor_state`: atualizado para `await applyContextMaskingDynamic(...)`

**Formatos de exibição por tipo** (migrados de `maskPiiValue()`):

```typescript
function applyMaskingType(value: unknown, type: ContextMaskingType): string | null {
  const str    = String(value ?? "")
  const digits = str.replace(/\D/g, "")
  switch (type) {
    case "plain":        return str                                          // valor original
    case "hidden":       return null                                         // campo removido
    case "full":         return "***"
    case "last_2":       return `***${digits.slice(-2)}`
    case "last_4":       return `***${digits.slice(-4)}`
    case "first_1":      return `${str[0] ?? "*"}***`
    case "first_word":   return `${str.split(" ")[0]} ***`
    case "email_domain": {
      const [local = "", domain = ""] = str.split("@")
      return `${local[0] ?? "*"}***@${domain}`
    }
    case "financial":    return "R$ ****,**"
  }
}
```

### Fase C — UI (MaskingPage)

**`platform-ui/src/modules/masking/MaskingPage.tsx`**

Nova seção "Regras de Context Store" (abaixo das seções existentes):

- Tabela editável de `ContextMaskingRule[]`
  - Colunas: Variável (pattern), Perfil (role), Tipo, Label, Ações
  - Filtros: por namespace (caller, account, …), por role
  - Coluna preview: exibe como ficaria `"123.456.789-00"` com o tipo selecionado
- Botão "Adicionar regra" → inline form: `pattern` (input com sugestões de tags conhecidas), `role` (select), `type` (select com preview ao vivo)
- "Salvar" → `PUT /config/masking/context_rules` com payload completo atualizado
- Campo "Comportamento padrão (operator)" → select de `default_unmatched_operator`
- Toast de confirmação + reload

### Fase D — Monitoramento (deferred)

Log estruturado quando `default_unmatched_operator` é aplicado a um campo dos namespaces `caller.*` ou `account.*`:

```json
{ "event": "context_masking_default_applied", "tag": "caller.passaporte", "tenant_id": "...", "role": "operator" }
```

Permite ao admin identificar campos PII que precisam de regra explícita.

---

## O que mudou em relação à implementação original (Fase 1)

As seguintes construções em `server.ts` foram substituídas pelas Fases A e B:

| Antes (hardcoded) | Depois (dinâmico) |
|---|---|
| `TAG_PII_CATEGORY` const | Config API `context_rules` via `MaskingService.loadContextMaskingConfig()` |
| `maskPiiValue(value, category)` | `applyMaskingTypeToValue(raw, type)` com 9 tipos visuais |
| `applyContextMasking()` síncrono | `applyContextMaskingDynamic()` assíncrono com cache |

O resultado visível no frontend e o comportamento para o usuário final são **idênticos** — o `DEFAULT_CONTEXT_MASKING_CONFIG` replica exatamente o `TAG_PII_CATEGORY` anterior. A diferença é que o administrador agora pode alterar as regras pela UI (Fase C, pendente) sem deploy.
