/**
 * audit.ts
 * Tipos de auditoria, categorias de dados LGPD e mascaramento.
 * Fonte da verdade: plughub_spec_v1.docx seção 13
 */

import { z } from "zod"
import { ParticipantRoleSchema } from "./common"

// ─────────────────────────────────────────────
// Categorias de dados LGPD
// ─────────────────────────────────────────────

export const DataCategorySchema = z.enum([
  "cpf",          // Cadastro de Pessoa Física
  "credit_card",  // número de cartão de crédito
  "phone",        // número de telefone
  "email_addr",   // endereço de e-mail
  "address",      // endereço residencial ou comercial
  "health",       // dados de saúde
  "financial",    // dados financeiros em geral
])
export type DataCategory = z.infer<typeof DataCategorySchema>

// ─────────────────────────────────────────────
// Política de auditoria — definida na tool, não por chamada
// ─────────────────────────────────────────────

/**
 * AuditPolicy — definida no registro da tool.
 * O caller NUNCA pode suprimir o registro de auditoria.
 * O caller pode apenas enriquecer via audit_context.
 */
export const AuditPolicySchema = z.object({
  data_categories:  z.array(DataCategorySchema).default([]),
  capture_input:    z.boolean().default(false),
  capture_output:   z.boolean().default(false),
  retention_days:   z.number().int().positive().default(365),
  requires_consent: z.boolean().default(false),
})
export type AuditPolicy = z.infer<typeof AuditPolicySchema>

/**
 * AuditContext — enriquecimento opcional por chamada.
 * Nunca substitui nem suprime a AuditPolicy da tool.
 */
export const AuditContextSchema = z.object({
  reason:         z.string().optional(),
  correlation_id: z.string().optional(),
})
export type AuditContext = z.infer<typeof AuditContextSchema>

// ─────────────────────────────────────────────
// Discriminadores de principal e confiança
// (ADR docs/adr/adr-human-approval-workflow-step.md §9.5)
// ─────────────────────────────────────────────

/**
 * PrincipalType — TIPO do autor de uma ação auditável, discriminador ortogonal
 * ao id do principal. Cada tipo vive na sua fonte nativa (single-source):
 *   human  → usuário (auth-api, login/JWT)
 *   agent  → agente IA (agent-registry, credencial de instância)
 *   system → sistema externo (credencial de webhook registrada; NUNCA um user)
 */
export const PrincipalTypeSchema = z.enum(["human", "agent", "system"])
export type PrincipalType = z.infer<typeof PrincipalTypeSchema>

/**
 * VerificationClass — grau de confiança na identidade do autor no momento da ação.
 *   claimed   → identificador asseverado (ex.: âncora/ANI, token bearer)
 *   possessed → posse provada (humano: sessão autenticada viva; máquina: assinatura/mTLS)
 * Mesmo eixo do OTP de posse de canal (adr-identity-channel-possession).
 */
export const VerificationClassSchema = z.enum(["claimed", "possessed"])
export type VerificationClass = z.infer<typeof VerificationClassSchema>

// ─────────────────────────────────────────────
// Mascaramento de dados sensíveis
// ─────────────────────────────────────────────

export const MaskingRuleSchema = z.object({
  pattern:              z.string().min(1),          // regex de detecção
  category:             DataCategorySchema,
  replacement:          z.string().min(1),          // placeholder para display humano puro (ex: "***.***.***-**")
  preserve_last_digits: z.number().int().min(0).optional(), // ex: 4 para cartão, 2 para CPF
  /**
   * preserve_pattern: regex de extração do trecho visível quando não é sufixo numérico.
   * Ex: para e-mail — preserva domínio: "(@.+)$"
   * Tem precedência sobre preserve_last_digits se ambos definidos.
   */
  preserve_pattern:     z.string().optional(),
})
export type MaskingRule = z.infer<typeof MaskingRuleSchema>

export const MaskingConfigSchema = z.object({
  tenant_id: z.string().min(1),
  rules:     z.array(MaskingRuleSchema).default([]),
})
export type MaskingConfig = z.infer<typeof MaskingConfigSchema>

export const MaskedResultSchema = z.object({
  original:            z.string(),
  masked:              z.string(),
  categories_detected: z.array(DataCategorySchema).default([]),
})
export type MaskedResult = z.infer<typeof MaskedResultSchema>

// ─────────────────────────────────────────────
// ContextStore field-level masking (dynamic rules)
// ─────────────────────────────────────────────

/**
 * ContextMaskingType — visual presentation applied to a ContextStore tag value
 * when delivered to a given role.
 *
 * These are purely display semantics — they carry no implied data-type semantics
 * (e.g. "last_4" works on CPF, contract number, credit card, etc.).
 *
 * Stored in Config API: namespace "masking", key "context_rules" (global default
 * seeded in config-api seed.py; tenant overrides via the Masking page).
 * Consumed by mcp-server via GET /config/masking (config-http-propagation arc) —
 * not by direct Redis reads.
 */
export const ContextMaskingTypeSchema = z.enum([
  "plain",        // no masking — show value as-is
  "hidden",       // remove the field entirely from the response
  "full",         // mask entire value → "***"
  "last_2",       // show only last 2 chars → "***XX"
  "last_4",       // show only last 4 chars → "***XXXX"
  "first_1",      // show only first char → "X***"
  "first_word",   // show only the first word, mask the rest
  "email_domain", // keep domain, mask local part → "X***@domain.com"
  "financial",    // generic financial mask → "R$ ****,**"
])
export type ContextMaskingType = z.infer<typeof ContextMaskingTypeSchema>

/**
 * ContextMaskingRule — maps a tag name pattern × role to a masking type.
 *
 * pattern: exact tag name ("caller.cpf") or glob with single wildcard
 *          ("caller.*" matches "caller.cpf", "caller.nome", etc.)
 *          "*" matches any tag.
 *
 * role:    "operator"  — agents / human operators in the Console
 *          "supervisor" — covers supervisor, admin, evaluator, reviewer
 *          "*"          — applies to all roles (base-layer wildcard)
 *
 * Resolution: most-specific match wins.
 * Specificity score: exact > glob > "*"; role "operator" > "supervisor" > "*".
 * Ties broken by position in the rules array (first wins).
 */
export const ContextMaskingRuleSchema = z.object({
  /** Exact tag name or glob pattern with optional trailing "*" */
  pattern: z.string().min(1),
  /** Role this rule applies to */
  role:    z.enum(["operator", "supervisor", "*"]),
  /** Masking type to apply when pattern × role match */
  type:    ContextMaskingTypeSchema,
  /** Optional human-readable label for the Config UI */
  label:   z.string().optional(),
})
export type ContextMaskingRule = z.infer<typeof ContextMaskingRuleSchema>

/**
 * ContextMaskingConfig — full set of rules for a tenant.
 *
 * Loaded from Config API on first request, cached in-process with TTL.
 * Falls back to global defaults when no tenant-level config exists.
 */
export const ContextMaskingConfigSchema = z.object({
  /**
   * Ordered list of masking rules.
   * Evaluation stops at the first matching rule (most-specific first).
   */
  rules: z.array(ContextMaskingRuleSchema).default([]),
  /**
   * Masking type applied when no rule matches a tag for the "operator" role.
   * "plain" is the permissive default (most ContextStore tags are non-PII).
   * Conservative deployments may set "hidden".
   */
  default_unmatched_operator: ContextMaskingTypeSchema.default("plain"),
  /**
   * Roles treated as "supervisor" category for masking (bypass the namespace
   * gate, see PII plain). Config-driven so "who is elevated" is UI-editable and
   * not fixed in code. Any role NOT in this list is treated as "operator".
   * Default preserves the previous hardcoded behavior.
   */
  supervisor_roles: z.array(z.string()).default(["supervisor", "admin", "evaluator", "reviewer"]),
})
export type ContextMaskingConfig = z.infer<typeof ContextMaskingConfigSchema>

/**
 * DEFAULT_CONTEXT_MASKING_RULES — global fallback rules.
 *
 * Converts the original hardcoded TAG_PII_CATEGORY map exactly:
 *   caller.cpf              → last_2   (operator)
 *   caller.cnpj             → last_2   (operator)
 *   caller.telefone         → last_4   (operator)
 *   caller.email            → email_domain (operator)
 *   account.numero_contrato → last_4   (operator)
 *   account.valor_fatura    → financial (operator)
 *   account.limite_credito  → hidden   (operator)
 *   caller.*                → last_4   (operator, catch-all for caller namespace)
 *   account.*               → financial (operator, catch-all for account namespace)
 *
 * supervisor/* → plain (no masking for elevated roles).
 */
export const DEFAULT_CONTEXT_MASKING_CONFIG: ContextMaskingConfig = {
  default_unmatched_operator: "plain",
  supervisor_roles: ["supervisor", "admin", "evaluator", "reviewer"],
  rules: [
    // ── exact rules (highest specificity) ──────────────────────────────────
    // caller.customer_id — internal reference id (not PII); plain so operators can
    // identify the customer / load history / 360 (exact beats the caller.* catch-all).
    { pattern: "caller.customer_id",      role: "operator",   type: "plain",        label: "ID interno do cliente (não-PII)" },
    { pattern: "caller.cpf",              role: "operator",   type: "last_2",       label: "CPF do cliente" },
    { pattern: "caller.cnpj",             role: "operator",   type: "last_2",       label: "CNPJ do cliente" },
    { pattern: "caller.telefone",         role: "operator",   type: "last_4",       label: "Telefone do cliente" },
    { pattern: "caller.email",            role: "operator",   type: "email_domain", label: "E-mail do cliente" },
    { pattern: "account.numero_contrato", role: "operator",   type: "last_4",       label: "Número do contrato" },
    { pattern: "account.valor_fatura",    role: "operator",   type: "financial",    label: "Valor da fatura" },
    { pattern: "account.limite_credito",  role: "operator",   type: "hidden",       label: "Limite de crédito" },
    // ── glob catch-alls (medium specificity) ────────────────────────────────
    { pattern: "caller.*",                role: "operator",   type: "last_4",       label: "Dados do cliente (genérico)" },
    { pattern: "account.*",               role: "operator",   type: "financial",    label: "Dados da conta (genérico)" },
    // ── supervisor: no masking on any field ────────────────────────────────
    { pattern: "*",                       role: "supervisor", type: "plain",        label: "Supervisor vê tudo sem máscara" },
  ],
}

// ─────────────────────────────────────────────
// Política de acesso ao original_content
// ─────────────────────────────────────────────

/**
 * MaskingAccessPolicy — define quais roles podem receber original_content
 * ao ler mensagens via session_context_get.
 *
 * Default: apenas evaluator e reviewer.
 * O tenant pode adicionar supervisor se necessário.
 * primary e specialist NUNCA recebem original_content — o AI opera via tokens.
 *
 * Redis key: {tenant_id}:masking:access_policy
 */
export const MaskingAccessPolicySchema = z.object({
  tenant_id:        z.string().min(1),
  authorized_roles: z.array(ParticipantRoleSchema).default(["evaluator", "reviewer"]),
})
export type MaskingAccessPolicy = z.infer<typeof MaskingAccessPolicySchema>

// ─────────────────────────────────────────────
// Catálogo de TIPOS de dado — declaração única (ADR adr-contextstore-allowlist D1, fase V2)
// ─────────────────────────────────────────────

/**
 * Classe LGPD do dado — eixo de autorização, retenção e auditoria.
 * Propriedade do TIPO, não eixo paralelo: `AuditPolicy.data_categories` precisa da
 * resposta *"isto é dado pessoal sensível?"*, que "que forma tem" não responde.
 */
export const LgpdClassSchema = z.enum([
  "pessoal",    // dado pessoal comum (LGPD art. 5º I)
  "sensivel",   // dado pessoal sensível (art. 5º II) — saúde, biometria, etc.
  "financeiro", // dado financeiro / PCI-DSS
  "credencial", // segredo ou capacidade (token, senha) — nunca retido
  "none",       // não pessoal
  // "não classificado" — o dado é mascarado e a CLASSE não foi declarada.
  // Não é `none`: `none` afirma *"não é dado pessoal"*, e essa é uma afirmação que
  // ninguém fez. Não é `sensivel` nem `credencial`: essas são afirmações jurídicas
  // que inflariam a contagem de qualquer relatório LGPD com dado que talvez não
  // seja daquela classe. É o mesmo padrão do balde `unknown` do rollup de
  // capacidade — publicado como classe PRÓPRIA e contado, nunca dobrado numa real,
  // porque dobrar escolhe a moeda cara em silêncio.
  "nao_classificado",
])
export type LgpdClass = z.infer<typeof LgpdClassSchema>

/**
 * DataTypeFormat — apresentação do valor quando visível E, quando existir, a
 * DETECÇÃO em texto livre.
 *
 * Todos os campos são opcionais por decisão (D1: `formato` pode ser vazio). Um tipo
 * SEM `detect_pattern` é declarável e legítimo — apenas não é detectável em texto
 * livre, e a tela deve dizer isso em vez de exibir "Ativo".
 *
 * ⚠️ Formatação é RENDER-TIME, nunca storage (ADR §5): gravar "R$ 1.234,56"
 * corromperia o valor que o agente passa ao CRM. E a ordem é declarada, não
 * emergente: **máscara opera no canônico; `display` só se aplica quando a máscara
 * é ∅ ou `plain`**.
 */
export const DataTypeFormatSchema = z.object({
  /** Máscara de apresentação — ex: "###.###.###-##", "R$ #.##0,00" */
  display:              z.string().optional(),
  /** Regex de DETECÇÃO em texto livre. Ausente = tipo não detectável. */
  detect_pattern:       z.string().optional(),
  /** Placeholder usado quando a detecção casa */
  replacement:          z.string().optional(),
  preserve_last_digits: z.number().int().min(0).optional(),
  preserve_pattern:     z.string().optional(),
})
export type DataTypeFormat = z.infer<typeof DataTypeFormatSchema>

/**
 * Como um TOKEN mascarado aflora quando a mensagem é renderizada.
 *
 * ── É CHANNEL-ABSTRACT (ALW-10, 2026-09-02) ─────────────────────────────────
 * Chamava-se `display_screen` e tinha um irmão `display_voice`
 * (`beep | silence | speak_placeholder`) — duas declarações para a mesma
 * intenção, uma por canal. A política é do TIPO; **traduzi-la é do adapter**,
 * que é o invariante que o `CLAUDE.md` já enuncia (*"never implement
 * channel-specific rendering logic outside channel-gateway"*).
 *
 *   `display_partial` — mostra o parcial embutido no token (`***-00`).
 *                       Em voz, o adapter fala o parcial.
 *   `full_mask`       — substituto não-informativo (`•••••`). Em voz: bipe ou
 *                       placeholder falado — escolha do adapter, não do tenant.
 *   `hidden`          — não aflora. Em voz: silêncio.
 *
 * ⚠️ O parcial existe AQUI e não no eco porque o token JÁ o carrega:
 * `[cpf:tk_b7d2:***-00]`. Não há re-mascaramento na borda; há leitura de um
 * campo que o produtor do token gravou.
 */
export const TokenDisplayModeSchema = z.enum(["display_partial", "full_mask", "hidden"])
export type TokenDisplayMode = z.infer<typeof TokenDisplayModeSchema>

/**
 * ECO — a entrada FRESCA do cliente volta, e em que forma?
 *
 * ── Sem parcial, e isso é estrutural (ALW-10, 2026-09-02) ───────────────────
 * Eco não renderiza token: devolve o que a pessoa acabou de digitar. Não há
 * `display_partial` embutido para ler, e produzir um exigiria re-mascarar na
 * borda — inventando uma terceira resposta para *"quanto do valor aparece"*,
 * ao lado de `mascara.by_role` (9 valores) e `TokenDisplayMode` (3).
 * Por isso o domínio é ternário e grosso: volta, não volta, ou volta cego.
 *
 *   `plain`  — volta como veio
 *   `none`   — não volta
 *   `masked` — volta um substituto NÃO-INFORMATIVO (`••••••`, bipe, silêncio…)
 *
 * ⚠️ **As duas pontas não têm a mesma força, e confundi-las seria caro.**
 * `echo_to_operator` é FRONTEIRA DE CONFIDENCIALIDADE — o operador não conhece
 * o valor, e a plataforma controla as três casas que o exibem.
 * `echo_to_customer` é ADVISORY: o cliente digitou o valor, já o conhece, e o
 * que a plataforma pode fazer é DECLARAR o modo no evento de interação para o
 * cliente do canal obedecer. É medida contra quem olha por cima do ombro, não
 * contra o titular do dado.
 *
 * ⚠️ **Isto não governa PERSISTÊNCIA.** Dos cinco destinos de
 * `redact_customer_reply` (bridge), três não são superfície de conversa —
 * Kafka→ClickHouse, log do bridge e o stream de Analytics. Foi neles que o
 * vazamento de 2026-08-29 aconteceu (`senha` e `codigo_2fa` no ClickHouse), e
 * quem os cobre continua sendo a redação por `masked`, não este campo.
 */
export const EchoModeSchema = z.enum(["plain", "none", "masked"])
export type EchoMode = z.infer<typeof EchoModeSchema>

/**
 * MaskingDisplayRule — a dimensão CANAL do mascaramento.
 * Vivia apenas em `platform-ui/src/components/MaskedToken.tsx` (sem contrato
 * compartilhado) e era gravada solta como `masking.rule.{category}` no config-api.
 * Passa a ser propriedade do tipo (§D1: `mascara` opcionalmente carrega canal).
 *
 * ⚠️ **Default de `echo_to_operator` é `masked`, e a migração do booleano segue
 * o COMPORTAMENTO, não o nome do campo.** O antigo `echo_to_operator: true`
 * lia-se "ecoa", mas o que as três casas fazem com ele é `••••••`
 * (`_MASKED_FIELD_PLACEHOLDER`). Mapear `true → plain` transformaria a política
 * vigente num vazamento no instante em que alguém ligasse o fio — ela virou
 * `masked`. Ver `infra/scripts/migrate_masking_display_rule.py`.
 */
export const MaskingDisplayRuleSchema = z.object({
  token_display:    TokenDisplayModeSchema.default("display_partial"),
  echo_to_customer: EchoModeSchema.default("none"),
  echo_to_operator: EchoModeSchema.default("masked"),
})
export type MaskingDisplayRule = z.infer<typeof MaskingDisplayRuleSchema>

/**
 * DataTypeMask — a dimensão PAPEL (e, opcionalmente, CANAL) do mascaramento.
 * `by_role` mapeia papel → ContextMaskingType. Pode ser vazio (D1).
 */
export const DataTypeMaskSchema = z.object({
  by_role: z.record(ContextMaskingTypeSchema).default({}),
  display: MaskingDisplayRuleSchema.optional(),
})
export type DataTypeMask = z.infer<typeof DataTypeMaskSchema>

/**
 * DataType — a declaração ÚNICA de um tipo de dado.
 *
 * Funde as três METADES que viviam em casas separadas, cada uma com a dimensão
 * que faltava às outras (ADR §1.4):
 *   `MaskingRule` + regex      → detecção, sem papel nem canal   → vira `formato`
 *   `MaskingDisplayRule`       → canal, sem papel                → vira `mascara.display`
 *   `ContextMaskingRule`/Type  → papel, sem detecção nem canal   → vira `mascara.by_role`
 * mais a classe LGPD, que nenhuma das três carregava.
 *
 * **Invariante: um campo declara EXATAMENTE UM tipo.** Se dois campos precisam de
 * políticas diferentes, são dois tipos — nunca um tipo com exceção no campo.
 */
/**
 * OPAQUE_DATA_TYPE_ID — o tipo para o qual `masked: true` resolve.
 *
 * Uma casa só para a string: engine, normalizador e guardas leem daqui, senão o id
 * do tipo mais restritivo do produto vira literal repetido em N arquivos, que é
 * como um rename silencioso desliga o mascaramento.
 */
export const OPAQUE_DATA_TYPE_ID = "opaque"

/**
 * `texto` — o tipo sem política. Constante pelo mesmo motivo de
 * `OPAQUE_DATA_TYPE_ID`: é referenciado pelo mapa do ContextStore e pelo oráculo,
 * e um literal repetido em três casas é como as grafias divergem.
 */
export const TEXTO_DATA_TYPE_ID = "texto" as const

/**
 * MaskedDeclaration — o que um step/nó ou campo declara em `masked`.
 *
 *   `"cpf"`  → mascarado, e o TIPO é `cpf` (política vem do catálogo)
 *   `false`  → não mascarado (override explícito sobre o step)
 *   ausente  → herda o step/nó
 *
 * ⚠️ **`true` NÃO é mais aceito na ESCRITA** (fase T7-A, 2026-08-29). Era a
 * declaração ANÔNIMA — dizia *"esconda"* sem dizer **o quê** —, e é ela que o arco
 * existe para eliminar: sem o tipo, máscara-por-papel e classe LGPD não têm onde
 * morar. Fechado por CONTADOR, não por decreto: o parque zerou na T6 (medido na
 * autoridade, não no arquivo) e o gate `q_masked_declaration_census.sh` § eixo 1b
 * o confere.
 *
 * **`false` FICA, e a assimetria é deliberada.** Ele tem zero usos hoje, e ainda
 * assim não sai junto: `false` é a única forma de dizer *"este campo NÃO é
 * mascarado, mesmo que o step mascare"*. Removê-lo tiraria uma CAPACIDADE; tirar
 * `true` remove uma FORMA LEGADA. Ausência de uso não é o mesmo que ausência de
 * propósito.
 *
 * ⚠️ **O RUNTIME segue tolerando `true`, de propósito** — ver `normalizeDecl` em
 * `skill-flow-engine/src/masking-policy.ts`. O snapshot de slot NÃO é validado por
 * Zod na execução (o skill-flow-service é wrapper fino) e o `POST /rollback` apenas
 * troca linhas de slot, sem revalidar. Logo um `previous` anterior à T6 continua
 * executável, e tem de continuar: remover a tolerância do runtime faria
 * `normalizeDecl` cair em `d.trim()` sobre um booleano — TypeError no meio de um
 * atendimento. Esta é a metade (B) da T7, adiada por decisão.
 */
export const MaskedDeclarationSchema = z.union([z.literal(false), z.string()])
export type MaskedDeclaration = z.infer<typeof MaskedDeclarationSchema>

export const DataTypeSchema = z.object({
  id:      z.string().min(1),
  label:   z.string().optional(),
  icon:    z.string().optional(),
  formato: DataTypeFormatSchema.default({}),
  mascara: DataTypeMaskSchema.default({}),
  lgpd:    LgpdClassSchema.default("none"),
  /**
   * Tipo alcançável APENAS por DECLARAÇÃO — nunca por detecção nem por ser um
   * `DataCategory` canônico.
   *
   * ⚠️ São DOIS sítios de declaração, e a V3 acrescentou o segundo: `masked: "<id>"`
   * num campo de formulário, e o **mapa do ContextStore** (`context-map.ts`). A
   * marca diz *"não se chega aqui por detecção"*, e não *"o `masked:` aceita"* —
   * quem decide o que cada sítio aceita é o sítio. `texto` é o caso que separa os
   * dois: declarável no mapa, recusado pelo `masked:` (ver `typeMasksSomething`).
   *
   * Existe por causa do ORÁCULO: `verifyDataTypeCatalog` trata como órfão todo tipo
   * sem `detect_pattern` cujo id não seja `DataCategory`, e foi assim que a V2
   * expulsou os fantasmas `iban`/`passport`. `opaque` é indetectável **por
   * construção** (é a resolução de `masked: true`, cujo conteúdo é desconhecido) e
   * não é uma espécie de dado, então não cabe no enum `DataCategory` — pô-lo lá
   * recriaria exatamente o fantasma que a V2 removeu: membro de enum que nenhum
   * produtor emite, já que campo opaco é SUPRIMIDO e nunca tokenizado.
   *
   * Marcar em vez de excetuar mantém o oráculo capaz de REPROVAR: um tipo sem
   * detecção, fora do enum e sem esta marca continua órfão.
   *
   * `.optional()` e não `.default(false)`: com `default`, o campo vira OBRIGATÓRIO
   * no tipo de saída do Zod e as 7 entradas literais teriam de repetir
   * `declared_only: false` sem acrescentar nada. Ausente já significa `false` para
   * o oráculo (`!t.declared_only`), então o default restritivo se preserva — mesma
   * escolha de `label` e `icon`.
   */
  declared_only: z.boolean().optional(),
})
export type DataType = z.infer<typeof DataTypeSchema>

/**
 * DataTypeCatalog — armazenado no Config API, namespace "masking", chave "types".
 * Fonte de verdade é o config-api; o seed apenas semeia base vazia (D7).
 */
export const DataTypeCatalogSchema = z.object({
  types: z.array(DataTypeSchema).default([]),
})
export type DataTypeCatalog = z.infer<typeof DataTypeCatalogSchema>

/**
 * DEFAULT_DATA_TYPE_CATALOG — o catálogo global semeado.
 *
 * **Contém apenas o que é ALCANÇÁVEL por um mecanismo existente** (decisão de
 * 2026-08-26, medida antes de tomada):
 *   · `formato.detect_pattern` presente ⇒ alcançável por DETECÇÃO em texto livre;
 *   · id ∈ `DataCategorySchema` ⇒ alcançável por declaração de tool
 *     (`AuditPolicy.data_categories`, lido em `sdk/src/mcp-interceptor.ts`).
 *
 * `iban` e `passport` NÃO entram: não estão no enum, não têm regra, não têm regex —
 * existiam só como card de tela com selo "Ativo" incondicional. Um tipo que nenhum
 * mecanismo alcança é o defeito que este catálogo existe para impedir, não um item
 * de backlog.
 *
 * Os valores de `mascara.by_role` NÃO são inventados: são a política VIVA medida no
 * config-api (`masking.context_rules`, 23 regras) — `caller.cpf → last_2`,
 * `caller.telefone`/`*.numero_cartao → last_4`, `caller.email → email_domain`.
 * `mascara.display` é `DEFAULT_DISPLAY_RULE` porque a medição de 2026-08-26 achou
 * ZERO chaves `masking.rule.*` gravadas — o default É o valor efetivo hoje.
 */
export const DEFAULT_DATA_TYPE_CATALOG: DataTypeCatalog = {
  types: [
    {
      id:    "cpf",
      label: "CPF",
      icon:  "🪪",
      formato: {
        display:              "###.###.###-##",
        detect_pattern:       "\\b\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}\\b",
        replacement:          "***.***.***.--",
        preserve_last_digits: 2,
      },
      mascara: {
        by_role: { operator: "last_2" },
        display: { token_display: "display_partial", echo_to_customer: "none", echo_to_operator: "masked" },
      },
      lgpd: "pessoal",
    },
    {
      id:    "credit_card",
      label: "Cartão de crédito",
      icon:  "💳",
      formato: {
        display:              "#### #### #### ####",
        detect_pattern:       "\\b(?:\\d{4}[\\s-]?){3}\\d{4}\\b",
        replacement:          "**** **** **** ****",
        preserve_last_digits: 4,
      },
      mascara: {
        by_role: { operator: "last_4" },
        display: { token_display: "display_partial", echo_to_customer: "none", echo_to_operator: "masked" },
      },
      lgpd: "financeiro",
    },
    {
      id:    "phone",
      label: "Telefone",
      icon:  "📞",
      formato: {
        display: "(##) #####-####",
        // ⚠️ O `\b` inicial foi trocado por `(?<!\w)` em 2026-08-26, e é conserto de
        // RAMO MORTO, não ajuste de gosto: `\b` exige transição \W→\w, que NUNCA
        // ocorre antes de um `(`. Logo o `\(?` desta alternativa jamais podia casar —
        // o match sempre começava no primeiro dígito e o parêntese de abertura ficava
        // órfão, colado na máscara, nas três portas de masking (`(***4321`,
        // `((##) ****-4321`, `([phone:tk_x:*******4321]`).
        // O conserto muda o TRECHO casado, nunca o CONJUNTO de telefones detectados:
        // os dígitos já eram detectados, só o `(` ficava de fora.
        detect_pattern:       "(?<!\\w)(?:\\+55\\s?)?(?:\\(?\\d{2}\\)?[\\s-]?)?9?\\d{4}[-\\s]?\\d{4}\\b",
        replacement:          "(##) ****-####",
        preserve_last_digits: 4,
      },
      mascara: {
        by_role: { operator: "last_4" },
        display: { token_display: "display_partial", echo_to_customer: "none", echo_to_operator: "masked" },
      },
      lgpd: "pessoal",
    },
    {
      id:    "email_addr",
      label: "E-mail",
      icon:  "📧",
      formato: {
        detect_pattern:   "\\b[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}\\b",
        replacement:      "****@****.***",
        preserve_pattern: "(@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,})$",
      },
      mascara: {
        by_role: { operator: "email_domain" },
        display: { token_display: "display_partial", echo_to_customer: "none", echo_to_operator: "masked" },
      },
      lgpd: "pessoal",
    },
    // ── Sem detecção em texto livre ────────────────────────────────────────────
    // Alcançáveis SÓ por declaração de tool (`AuditPolicy.data_categories`).
    // Medido em 2026-08-26: NENHUMA tool do repositório declara `audit_policy`,
    // então hoje não há produtor — o caminho existe, o uso não. A tela mostra isso
    // ("declarado, não detecta"), em vez do selo "Ativo" que mentia.
    {
      id:      "address",
      label:   "Endereço",
      icon:    "🏠",
      formato: {},
      mascara: { by_role: { operator: "first_word" } },
      lgpd:    "pessoal",
    },
    {
      id:      "health",
      label:   "Dados de saúde",
      icon:    "🩺",
      formato: {},
      mascara: { by_role: { operator: "full" } },
      lgpd:    "sensivel",
    },
    {
      id:      "financial",
      label:   "Dados financeiros",
      icon:    "🏦",
      formato: { display: "R$ #.##0,00" },
      mascara: { by_role: { operator: "financial" } },
      lgpd:    "financeiro",
    },
    // ── credential — segredo que prova identidade (senha, código 2FA) ─────────
    //
    // Alvo da T6: é para cá que migram as declarações `masked: true` de
    // `skill_auth_form_v1` (senha, codigo_2fa) e `skill_auth_ia_v1`.
    //
    // Indetectável por construção (uma senha não tem forma), daí `declared_only`.
    // Máxima restrição, igual ao `opaque` — o que muda é a CLASSE, e é ela que
    // justifica ser um tipo à parte em vez de continuar anônimo: `credencial` diz
    // a um relatório LGPD *o que* foi coletado, coisa que `opaque` não sabe dizer.
    //
    // Senha e código 2FA compartilham este tipo porque compartilham a POLÍTICA
    // (ninguém vê, nunca persiste, classe credencial). O que os distingue viaja no
    // ID DO CAMPO, que é a chave do `masked_types` — a distinção não se perde.
    {
      id:      "credential",
      label:   "Credencial (senha, código 2FA, token de retomada)",
      icon:    "🔑",
      formato: {},
      mascara: {
        by_role: { operator: "hidden" },
        display: { token_display: "hidden", echo_to_customer: "none", echo_to_operator: "none" },
      },
      lgpd:          "credencial",
      declared_only: true,
    },
    // ── card_cvv — o código de segurança do cartão ────────────────────────────
    //
    // **Não é `credit_card`**, e a diferença não é de nome: `credit_card` declara
    // `display_partial` com os 4 últimos dígitos, e num CVV de 3 dígitos isso
    // exibiria quase o valor inteiro. Reusar o tipo do PAN seria a economia que
    // vaza.
    //
    // **Nem é `credential`**, apesar da política idêntica: a CLASSE difere
    // (`financeiro` × `credencial`), e a classe é propriedade do tipo (D1) —
    // "se dois campos precisam de políticas diferentes, são dois tipos".
    {
      id:      "card_cvv",
      label:   "CVV do cartão",
      icon:    "🔒",
      formato: {},
      mascara: {
        by_role: { operator: "hidden" },
        display: { token_display: "hidden", echo_to_customer: "none", echo_to_operator: "none" },
      },
      lgpd:          "financeiro",
      declared_only: true,
    },
    // ── opaque — a resolução de `masked: true` ────────────────────────────────
    //
    // Fase T1 do ADR `adr-masked-typed-declaration.md`. Quando `masked` passar a
    // aceitar um id de tipo, `true` resolve AQUI — e o ponto é que `true` deixa de
    // ser *"mascarado sem tipo"* e passa a ser *"mascarado do tipo mais
    // restritivo"*. Manter um ramo sem tipo reintroduziria o default permissivo na
    // forma mais cara: como AUSÊNCIA, o valor mais barato de produzir e o mais
    // difícil de contar.
    //
    // Máxima restrição, e cada campo é escolha, não preenchimento:
    //   · sem `formato.detect_pattern` — indetectável por construção (não se sabe
    //     o que é), daí `declared_only`;
    //   · `by_role.operator: "hidden"` — o operador não vê. `hidden` remove o campo,
    //     e é o mais forte do `ContextMaskingType`;
    //   · `display`: não aflora em canal nenhum e **não ecoa para ninguém** —
    //     `echo_to_operator: "none"` é o único do catálogo, e é deliberado;
    //   · `lgpd: "nao_classificado"` — ver o comentário do enum. Dizer `none` seria
    //     afirmar que não é dado pessoal; dizer `sensivel`/`credencial` seria uma
    //     afirmação jurídica que ninguém fez.
    {
      id:      OPAQUE_DATA_TYPE_ID,
      label:   "Não classificado (mascarado sem tipo)",
      icon:    "⬛",
      formato: {},
      mascara: {
        by_role: { operator: "hidden" },
        display: { token_display: "hidden", echo_to_customer: "none", echo_to_operator: "none" },
      },
      lgpd:          "nao_classificado",
      declared_only: true,
    },
    // ── card_expiry — o vencimento do cartão (MM/AA) ──────────────────────────
    //
    // Existe porque o campo tem POLÍTICA VIVA e não tinha tipo: as duas regras do
    // tenant (`session.vencimento_cartao` exata e `*.vencimento_cartao`) o mascaram
    // `last_2`, e nenhum dos 11 tipos anteriores casava máscara E classe. Enquanto
    // faltasse, o campo ficava FORA do mapa de propósito e a auditoria — corretamente
    // — não autorizava a V4. A lacuna era do CATÁLOGO, e fecha-se aqui.
    //
    // **Não é `credit_card`**: aquele preserva os 4 últimos, e sobre `12/26` (dígitos
    // `1226`) isso devolveria o valor INTEIRO. Mesmo argumento pelo qual a T6 recusou
    // reusar `credit_card` no CVV.
    // **Nem é `cpf`**, apesar de a máscara coincidir (`last_2`): a CLASSE difere
    // (`financeiro` × `pessoal`), e classe é propriedade do tipo (D1).
    //
    // `last_2` sobre `12/26` → `***26`: aparece o ANO, some o MÊS. Este tipo DECLARA
    // a política que já vale hoje; não a muda.
    //
    // ⚠️ **Tipo de LEITURA, nunca de COLETA.** Ele mascara algo, logo passa no portão
    // da T5 e pode ser escrito num `masked:` — e isso QUEBRARIA o pacote de aprovação
    // em silêncio: pela D4 do ADR do `masked` tipado, masked nunca entra em
    // `pipeline_state`, e é de lá que `skill_limite_entrada_v1.yaml:475` lê o valor
    // para escrever a tag. O form declara `masked` só no `cvv`, e isso é desenho —
    // ver `infra/dialog/dialog_limite_solicitacao.json`.
    {
      id:    "card_expiry",
      label: "Vencimento do cartão (MM/AA)",
      icon:  "📅",
      // Sem `detect_pattern` de propósito: `\d{2}/\d{2}` casaria qualquer data.
      formato: { display: "##/##" },
      mascara: {
        by_role: { operator: "last_2" },
        display: { token_display: "display_partial", echo_to_customer: "none", echo_to_operator: "masked" },
      },
      lgpd:          "financeiro",
      declared_only: true,
    },
    // ── linha_em_servico — o telefone que é o OBJETO do atendimento ───────────
    //
    // Primeiro tipo cuja razão de existir é a FINALIDADE, não o formato (decisão do
    // dono, 2026-08-30). Dado de CADASTRO — a linha do cliente,
    // `session.cliente.telefone` — é protegido; o número que é o OBJETO da transação
    // (a linha sendo portada) não, porque não se atende portabilidade sem vê-lo.
    //
    // Até aqui o tipo amarrava formato × máscara × classe e **não tinha eixo para
    // finalidade**, então `session.cliente.telefone` e
    // `session.portabilidade.numero_atual` eram o MESMO `phone`. A finalidade entra
    // como TIPO, e não como exceção de regra, porque mapa e regra dando respostas
    // diferentes é a duplicação que este arco existe para matar — e a permissiva
    // venceria.
    //
    // `lgpd: "pessoal"` FICA, e é o ponto: um telefone continua identificando alguém.
    // O que se declara vazio é a MÁSCARA, nunca a CLASSE — um relatório LGPD tem de
    // seguir dizendo que um telefone foi coletado. `texto` (`lgpd: "none"`) seria a
    // economia que mente.
    //
    // `declared_only` é EXIGÊNCIA, não conveniência: a detecção olha o VALOR, e o
    // valor não diz a finalidade — dois tipos com o mesmo regex seriam ambíguos em
    // texto livre. É a D5 do ADR do `masked` tipado (*"a detecção fica fora"*).
    //
    // ⚠️ `by_role` vazio ⇒ INELEGÍVEL a `masked:` (`typeMasksSomething`), como o
    // `texto`. Correto: tipo que não esconde nada não pode declarar que algo está
    // escondido.
    //
    // ⚠️ O nome é a FINALIDADE, não o formato. Um `phone_open` seria arma carregada
    // apontada para o próximo telefone de cadastro.
    {
      id:      "linha_em_servico",
      label:   "Linha em serviço (telefone que é objeto do atendimento)",
      icon:    "📱",
      formato: { display: "(##) #####-####" },
      // `by_role` VAZIO — ver o comentário acima. Não é omissão.
      mascara: { by_role: {} },
      lgpd:          "pessoal",
      declared_only: true,
    },
    // ── valor_declarado_pelo_cliente — o segundo tipo de FINALIDADE ──────────
    //
    // Mesmo molde do `linha_em_servico` (D8, 2026-08-30): a razão de existir é a
    // FINALIDADE, não o formato, e por isso a máscara é vazia enquanto a **classe LGPD
    // é preservada**. Aqui o valor é financeiro e continua `financeiro`; o que se
    // declara vazio é a máscara, nunca a classe.
    //
    // ── Por que ele nasceu (2026-09-02) ──────────────────────────────────────
    //
    // O `preview` de uma pendência mostra ao cliente o que ele mesmo pediu — no fluxo de
    // aumento de limite, `limite_solicitado`. O tipo daquele campo era `financial`, cuja
    // máscara de operador é `financial`; aplicá-la ao CLIENTE esconderia dele o valor que
    // ele acabou de solicitar.
    //
    // A saída NÃO foi acrescentar um eixo de audiência ao catálogo (decisão do dono): o
    // eixo que resolve já existe e é a finalidade. Um valor que o próprio titular
    // declarou não é dado a proteger DELE — é o objeto da conversa, exatamente como a
    // linha sendo portada.
    //
    // ⚠️ O nome é a FINALIDADE, não o formato. Um `valor_aberto` ou `numero_plain` seria
    // arma carregada apontada para o próximo campo financeiro — que é precisamente o que
    // o comentário do `linha_em_servico` avisa sobre `phone_open`.
    //
    // ⚠️ NÃO usar para valor que a EMPRESA decidiu (limite aprovado, saldo, fatura):
    // aqueles o cliente não declarou, e a assimetria é o critério. `limite_aprovado`
    // segue `financial`.
    {
      id:      "valor_declarado_pelo_cliente",
      label:   "Valor declarado pelo próprio cliente (objeto do pedido)",
      icon:    "🧾",
      formato: {},
      // `by_role` VAZIO — ver o comentário acima. Não é omissão.
      mascara: { by_role: {} },
      lgpd:          "financeiro",
      declared_only: true,
    },
    // ── texto — o tipo que NÃO faz nada, e por que ele precisa existir ────────
    //
    // Acrescentado na V3 do arco ALLOWLIST. O mapa do ContextStore (D2) declara
    // TODO campo, e a maioria dos campos medidos é encanamento sem PII
    // (`core.pool.id`, `session.workflow.current_round`, `session.survey.grain`).
    // Sem um tipo para eles restariam duas saídas, ambas já recusadas por escrito:
    //
    //   · `tipo` opcional — reintroduz o "declarado porém sem tipo", que é o
    //     default permissivo na forma de AUSÊNCIA (a D1 do ADR do `masked` tipado
    //     recusa exatamente isto: o valor mais barato de produzir e o mais difícil
    //     de contar);
    //   · um segundo vocabulário no mapa (`tipo: "none"`) — o oitavo inventário de
    //     categoria, num arco que existe para colapsar sete.
    //
    // A D1 já previa este tipo: *"qualquer das três pode ser vazia — existe tipo
    // que só formata, e tipo que NÃO FAZ NADA"*.
    //
    // ⚠️ `by_role` VAZIO é o que o torna inelegível a `masked:` — ver
    // `typeMasksSomething` abaixo. Um campo declarado `masked: "texto"` seria
    // declarado-mascarado e renderizado em CLARO: fail-open com cara de
    // conformidade. O portão de deploy da T5 recusa, e o gate tem testemunha.
    {
      id:      TEXTO_DATA_TYPE_ID,
      label:   "Texto sem classificação (encanamento, ids internos)",
      icon:    "📄",
      formato: {},
      // `by_role` explicitamente VAZIO — é a declaração de que não há máscara para
      // papel nenhum, e é o que `typeMasksSomething` lê para recusar `masked:`.
      mascara: { by_role: {} },
      lgpd:          "none",
      declared_only: true,
    },
  ],
}

// ─────────────────────────────────────────────
// Regras de mascaramento padrão (defaults do sistema)
// ─────────────────────────────────────────────

/**
 * DEFAULT_MASKING_RULES — aplicadas quando o tenant não configurou regras próprias.
 * Alinhadas com LGPD e PCI-DSS.
 *
 * ⚠️ **DERIVADO do catálogo, nunca redigitado.** Era uma lista literal, e essa lista
 * era o 2º de SETE inventários de categoria que já haviam divergido entre si. A
 * derivação é o mecanismo que impede o 8º: não há como acrescentar uma regra sem
 * declarar o tipo, nem declarar um tipo detectável que não vire regra.
 *
 * A ordem do catálogo é a ordem das regras (as 4 detectáveis vêm primeiro, na mesma
 * ordem do literal anterior) — o masking aplica na sequência, então ordem é contrato.
 */
export const DEFAULT_MASKING_RULES: MaskingRule[] = DEFAULT_DATA_TYPE_CATALOG.types
  .filter(t =>
    typeof t.formato.detect_pattern === "string" &&
    typeof t.formato.replacement === "string" &&
    DataCategorySchema.safeParse(t.id).success
  )
  .map(t => {
    const rule: MaskingRule = {
      pattern:     t.formato.detect_pattern as string,
      category:    t.id as DataCategory,
      replacement: t.formato.replacement as string,
    }
    if (typeof t.formato.preserve_last_digits === "number") rule.preserve_last_digits = t.formato.preserve_last_digits
    if (typeof t.formato.preserve_pattern === "string")     rule.preserve_pattern     = t.formato.preserve_pattern
    return rule
  })

/**
 * typeMasksSomething — este tipo esconde algo de alguém?
 *
 * Existe porque a V3 acrescentou ao catálogo o primeiro tipo que **não mascara**
 * (`texto`), e com ele um caminho fail-open que antes não podia existir: o portão
 * de deploy da T5 (`invalid_masked_type`) conferia apenas que o id EXISTE no
 * catálogo, então `masked: "texto"` passaria — um campo declarado mascarado,
 * renderizado em claro, e com o selo de conformidade de quem declarou.
 *
 * É a pior forma do "valor plausível" que a § Postura de Engenharia cataloga: a
 * declaração está lá, a tela mostra o campo protegido na config, e o valor sai
 * inteiro. Um tipo que não mascara é legítimo — no MAPA, onde declarar encanamento
 * é o esperado; nunca no `masked:`, cuja única razão de ser é esconder.
 *
 * O predicado é derivado do próprio tipo (`by_role`), não de uma lista de exceção:
 * uma lista envelheceria no primeiro tipo novo, que é como o `iban`/`passport`
 * sobreviveram até a V2. Medido em 2026-08-29 contra o catálogo vivo: os 10 tipos
 * anteriores declaram `by_role.operator` não-`plain`, logo o portão não regride
 * nenhuma declaração existente.
 */
export function typeMasksSomething(t: DataType): boolean {
  const byRole = t.mascara?.by_role ?? {}
  return Object.values(byRole).some(v => v !== "plain")
}

/**
 * verifyDataTypeCatalog — o ORÁCULO do gate da V2, exportado para não ser reimplementado
 * pelo teste (um gate que reconstrói a regra que julga testa a si mesmo).
 *
 * Devolve as DUAS listas, porque um lado só não julga (ADR §7):
 *   · `orphan_types`            — tipo que nenhum mecanismo alcança (o caso `iban`/`passport`)
 *   · `categories_without_type` — valor do enum sem tipo declarado (o inverso)
 * Ambas vazias ⇒ os dois inventários fecham. Vazias sobre catálogo VAZIO não é
 * aprovação — por isso `declared` viaja junto, como testemunha de presença.
 */
export function verifyDataTypeCatalog(catalog: DataTypeCatalog = DEFAULT_DATA_TYPE_CATALOG): {
  declared: number
  orphan_types: string[]
  categories_without_type: string[]
} {
  const ids = catalog.types.map(t => t.id)
  // TRÊS alcances, e o terceiro entrou na T1 do ADR do `masked` tipado:
  //   detecção (`detect_pattern`) · categoria canônica (`DataCategory`) ·
  //   DECLARAÇÃO (`declared_only`, para o tipo que só pode ser nomeado por
  //   `masked: "<id>"` — hoje só `opaque`).
  // A marca é por tipo, e não uma lista de exceção no oráculo, para que um tipo
  // sem detecção, fora do enum e sem a marca continue órfão — o oráculo tem de
  // seguir capaz de REPROVAR, que é o que o ramo E do gate confere.
  const orphan_types = catalog.types
    .filter(t => !t.formato.detect_pattern
              && !DataCategorySchema.safeParse(t.id).success
              && !t.declared_only)
    .map(t => t.id)
  const categories_without_type = DataCategorySchema.options.filter(c => !ids.includes(c))
  return { declared: catalog.types.length, orphan_types, categories_without_type }
}

// ─────────────────────────────────────────────
// Registro de auditoria de MCP — tópico mcp.audit
// ─────────────────────────────────────────────

/**
 * AuditRecord — evento publicado no Kafka (tópico mcp.audit) a cada chamada
 * a um domain MCP Server, seja via McpInterceptor (em-processo) ou proxy sidecar.
 *
 * Invariante: o caller nunca pode suprimir este registro.
 * O caller pode apenas enriquecer via audit_context.
 *
 * Spec: PlugHub seção 9 — MCP interception / audit policy.
 */
export const AuditRecordSchema = z.object({
  event_type:          z.literal("mcp.tool_call"),
  timestamp:           z.string().datetime(),
  tenant_id:           z.string(),
  session_id:          z.string(),
  /** instance_id do agente via JWT; "unknown" quando não disponível (proxy sidecar) */
  instance_id:         z.string().optional(),
  /** Nome do domain MCP Server — ex: "mcp-server-crm" */
  server_name:         z.string(),
  /** Nome da tool invocada — ex: "customer_get" */
  tool_name:           z.string(),
  /** true = chamada foi encaminhada; false = bloqueada por permissão ou injection */
  allowed:             z.boolean(),
  /** Lista de permissões extraídas do JWT (permissions[]) */
  permissions_checked: z.array(z.string()),
  /** true quando injection_guard detectou padrão malicioso */
  injection_detected:  z.boolean(),
  /** pattern_id do injection_guard quando injection_detected = true */
  injection_pattern:   z.string().optional(),
  /** Latência total da chamada (0 se bloqueada antes do encaminhamento) */
  duration_ms:         z.number().nonnegative(),
  /** Categorias de dados LGPD sensíveis presentes na tool (audit_policy.data_categories) */
  data_categories:     z.array(DataCategorySchema).optional(),
  /** Snapshot do input — capturado apenas quando audit_policy.capture_input = true */
  input_snapshot:      z.unknown().optional(),
  /** Snapshot do output — capturado apenas quando audit_policy.capture_output = true */
  output_snapshot:     z.unknown().optional(),
  /** Enriquecimento opcional por chamada (nunca suprime a política da tool) */
  audit_context:       AuditContextSchema.optional(),
  /**
   * Origem do registro. São TRÊS bordas de interceptação, não duas:
   *   in_process       — McpInterceptor no processo do agente nativo (SDK)
   *   proxy_sidecar    — plughub-sdk proxy, agente externo que chama o domain server direto
   *   mcp_server_invoke — tool `invoke` do mcp-server-plughub, agente external-mcp
   *                       (a interceptação acontece server-side, não no agente)
   */
  source:              z.enum(["in_process", "proxy_sidecar", "mcp_server_invoke"]),
  /**
   * Campos cujos valores foram omitidos por serem mascarados (originados do masked_scope).
   * Registra QUAIS campos foram enviados, mas nunca seus valores.
   * Presente quando a tool recebe inputs via namespace @masked.*.
   * Quando todos os inputs são mascarados, input_snapshot = null.
   */
  masked_input_fields: z.array(z.string()).optional(),
  /**
   * R7a — simétrico ao input: paths (dot-notation) do output cujo conteúdo continha
   * PII e foi mascarado antes de persistir o `output_snapshot`. O snapshot NUNCA
   * carrega o valor cru de PII (fix de vazamento). As categorias detectadas no output
   * são unidas a `data_categories`. Presente quando capture_output=true e o retorno
   * da tool casou alguma regra de masking.
   */
  masked_output_fields: z.array(z.string()).optional(),
})
export type AuditRecord = z.infer<typeof AuditRecordSchema>
