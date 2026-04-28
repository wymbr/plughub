/**
 * lib/masking.ts
 * MaskingService — substitui dados sensíveis por tokens compostos no stream canônico.
 *
 * Token format: [{category}:{token_id}:{display_partial}]
 *
 *   [credit_card:tk_a8f3:****1234]   → AI vê "****1234", tool resolve número completo
 *   [cpf:tk_b7d2:***-00]             → AI vê "***-00", tool resolve CPF completo
 *   [phone:tk_c1e9:(11) ****-4321]   → AI vê "(11) ****-4321"
 *   [email_addr:tk_d4f0:j***@empresa.com] → AI vê domínio preservado
 *
 * O agente AI usa o display_partial para confirmar dados com o cliente.
 * O MCP Tool usa o token_id para resolver o valor completo no TokenVault.
 *
 * Referência: plughub_spec_v1.docx seção 13 — Mascaramento LGPD
 */

import type { MessageContent, MaskingConfig, MaskingRule, DataCategory, MaskingAccessPolicy } from "@plughub/schemas"
import { DEFAULT_MASKING_RULES } from "@plughub/schemas"
import type { ParticipantRole } from "@plughub/schemas"
import type { TokenVault }       from "./token-vault"

// ─────────────────────────────────────────────
// Resultado do mascaramento
// ─────────────────────────────────────────────

export interface MaskingResult {
  /** Conteúdo com tokens inline — entregue ao agente AI */
  tokenized_content:   MessageContent
  /** Conteúdo original intacto — persistido em original_content do stream */
  original_content:    MessageContent
  /** True se algum dado sensível foi detectado */
  masked:              boolean
  /** Categorias detectadas nesta mensagem */
  categories_detected: DataCategory[]
}

// ─────────────────────────────────────────────
// MaskingService
// ─────────────────────────────────────────────

export class MaskingService {

  /**
   * Aplica mascaramento com tokenização a um MessageContent.
   *
   * Apenas mensagens do tipo "text" são processadas — outros tipos (image, audio,
   * file, etc.) passam intactos (sem dados sensíveis no campo text).
   *
   * @param content    - conteúdo original da mensagem
   * @param config     - configuração de mascaramento do tenant (ou undefined para usar defaults)
   * @param vault      - TokenVault para geração dos tokens
   * @param tenantId   - tenant_id — escopo dos tokens no Redis
   * @param ttlSeconds - TTL dos tokens (deve coincidir com o TTL da sessão)
   */
  static async applyMasking(
    content:    MessageContent,
    config:     MaskingConfig | null,
    vault:      TokenVault,
    tenantId:   string,
    ttlSeconds: number
  ): Promise<MaskingResult> {
    // Apenas texto é processado
    if (content.type !== "text" || !content.text) {
      return {
        tokenized_content:   content,
        original_content:    content,
        masked:              false,
        categories_detected: [],
      }
    }

    const rules: MaskingRule[] = config?.rules?.length
      ? config.rules
      : DEFAULT_MASKING_RULES

    const original_text         = content.text
    let   tokenized_text        = original_text
    const categories_detected:  DataCategory[] = []

    // Aplica cada regra sequencialmente
    // Importante: processar do início ao fim — tokens já inseridos não são
    // re-processados porque o padrão [category:tk_xxx:display] não casa
    // com os regex das categorias de dados sensíveis.
    for (const rule of rules) {
      let regex: RegExp
      try {
        regex = new RegExp(rule.pattern, "g")
      } catch {
        // Regex inválida — ignora esta regra (não deve acontecer com defaults)
        continue
      }

      const matches = Array.from(tokenized_text.matchAll(regex))
      if (matches.length === 0) continue

      // Processa matches de trás para frente para preservar índices
      for (let i = matches.length - 1; i >= 0; i--) {
        const match = matches[i]
        if (!match) continue
        const full_match = match[0]
        const start      = match.index ?? 0
        const end        = start + full_match.length

        // Calcula display parcial
        const display = MaskingService.buildDisplay(full_match, rule)

        // Gera token no vault
        const token = await vault.generate(
          tenantId,
          rule.category,
          full_match,
          display,
          ttlSeconds
        )

        // Substitui no texto tokenizado
        tokenized_text =
          tokenized_text.slice(0, start) +
          token.inline +
          tokenized_text.slice(end)

        if (!categories_detected.includes(rule.category)) {
          categories_detected.push(rule.category)
        }
      }
    }

    const masked = categories_detected.length > 0

    return {
      tokenized_content: masked
        ? { ...content, text: tokenized_text }
        : content,
      original_content:  content,
      masked,
      categories_detected,
    }
  }

  /**
   * Constrói o display parcial para um match, seguindo a regra:
   *
   * 1. Se `preserve_pattern` definido — extrai o grupo 1 (ou match completo) do padrão
   *    Ex: email "joao@empresa.com" + preserve_pattern "(@.+)$" → "j***@empresa.com"
   *
   * 2. Se `preserve_last_digits` definido — mantém os últimos N dígitos
   *    Ex: "4539 1234 5678 1234" + preserve_last_digits 4 → "****1234"
   *
   * 3. Fallback — usa replacement completo (sem parcial)
   */
  private static buildDisplay(match: string, rule: MaskingRule): string {
    // Prioridade 1: preserve_pattern
    if (rule.preserve_pattern) {
      try {
        const re     = new RegExp(rule.preserve_pattern)
        const result = re.exec(match)
        if (result) {
          const preserved = result[1] ?? result[0]
          const prefix    = match.slice(0, match.length - preserved.length)
          const maskedLen = Math.max(1, Math.ceil(prefix.length / 4))
          return `${"*".repeat(maskedLen)}${preserved}`
        }
      } catch { /* fallback */ }
    }

    // Prioridade 2: preserve_last_digits
    if (rule.preserve_last_digits && rule.preserve_last_digits > 0) {
      const digits_only = match.replace(/\D/g, "")
      if (digits_only.length > rule.preserve_last_digits) {
        const tail     = digits_only.slice(-rule.preserve_last_digits)
        const maskLen  = digits_only.length - rule.preserve_last_digits
        return `${"*".repeat(maskLen)}${tail}`
      }
    }

    // Fallback: usa o replacement da regra
    return rule.replacement
  }

  // ─────────────────────────────────────────────
  // Controle de acesso ao original_content
  // ─────────────────────────────────────────────

  /**
   * Verifica se um role está autorizado a receber original_content.
   *
   * Retorna true apenas se o role constar na lista authorized_roles da policy do tenant.
   * primary e specialist nunca devem estar nessa lista — operam via tokens.
   */
  static canReadOriginalContent(
    role:   ParticipantRole,
    policy: MaskingAccessPolicy
  ): boolean {
    return policy.authorized_roles.includes(role)
  }

  /**
   * Carrega MaskingConfig do Redis para o tenant.
   * Key: {tenantId}:masking:config
   * Retorna null se não configurado (MaskingService usará DEFAULT_MASKING_RULES).
   */
  static async loadConfig(
    redis:    { get(key: string): Promise<string | null> },
    tenantId: string
  ): Promise<MaskingConfig | null> {
    try {
      const raw = await redis.get(`${tenantId}:masking:config`)
      if (!raw) return null
      return JSON.parse(raw) as MaskingConfig
    } catch {
      return null
    }
  }

  /**
   * Carrega MaskingAccessPolicy do Redis para o tenant.
   *
   * Lookup chain (first found wins):
   *   1. {tenantId}:masking:access_policy — legacy key, explicit override
   *   2. plughub:cfg:{tenantId}:masking:authorized_roles — Config API tenant-level cache
   *   3. plughub:cfg:__global__:masking:authorized_roles — Config API global default
   *   4. Hardcoded default: ["evaluator", "reviewer"]
   *
   * This means masking access policy is editable via ConfigPanel (Config API UI)
   * without requiring a separate admin endpoint.
   */
  static async loadAccessPolicy(
    redis:    { get(key: string): Promise<string | null> },
    tenantId: string
  ): Promise<MaskingAccessPolicy> {
    // 1. Legacy key (explicit override — highest priority)
    try {
      const raw = await redis.get(`${tenantId}:masking:access_policy`)
      if (raw) return JSON.parse(raw) as MaskingAccessPolicy
    } catch { /* continue */ }

    // 2. Config API tenant-level cache (managed via ConfigPanel)
    try {
      const raw = await redis.get(`plughub:cfg:${tenantId}:masking:authorized_roles`)
      if (raw) {
        const roles = JSON.parse(raw) as string[]
        if (Array.isArray(roles) && roles.length > 0) {
          return { tenant_id: tenantId, authorized_roles: roles }
        }
      }
    } catch { /* continue */ }

    // 3. Config API global default cache
    try {
      const raw = await redis.get(`plughub:cfg:__global__:masking:authorized_roles`)
      if (raw) {
        const roles = JSON.parse(raw) as string[]
        if (Array.isArray(roles) && roles.length > 0) {
          return { tenant_id: tenantId, authorized_roles: roles }
        }
      }
    } catch { /* continue */ }

    // 4. Hardcoded default
    return {
      tenant_id:        tenantId,
      authorized_roles: ["evaluator", "reviewer"],
    }
  }

  /**
   * Persiste MaskingAccessPolicy no Redis (legacy key).
   * Chamado por admin endpoints que escrevem diretamente a policy.
   * Nota: editar via Config API (namespace masking, key authorized_roles) é a
   * forma preferida — use loadAccessPolicy para leitura, que já faz fallback.
   */
  static async saveAccessPolicy(
    redis:    { set(key: string, value: string): Promise<unknown> },
    tenantId: string,
    policy:   MaskingAccessPolicy
  ): Promise<void> {
    await redis.set(`${tenantId}:masking:access_policy`, JSON.stringify(policy))
  }
}
