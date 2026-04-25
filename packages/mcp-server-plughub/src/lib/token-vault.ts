/**
 * lib/token-vault.ts
 * TokenVault — armazena e resolve tokens para dados sensíveis mascarados.
 *
 * Cada token representa um dado sensível detectado em uma mensagem.
 * O token carrega um partial display visível (ex: "****1234") para que
 * o agente AI possa confirmar dados com o cliente sem expor o valor completo.
 *
 * Redis key: {tenantId}:token:{tokenId}
 * TTL: igual ao da sessão (padrão 4h)
 *
 * Formato do token no stream:
 *   [{category}:{tokenId}:{displayPartial}]
 *   Ex: [credit_card:tk_a8f3c2:****1234]
 *       [cpf:tk_b7d2e1:***-00]
 *       [phone:tk_c1e9f3:(11) ****-4321]
 *       [email_addr:tk_d4f0a2:j***@empresa.com]
 */

import { randomBytes } from "crypto"
import type { DataCategory } from "@plughub/schemas"

// ─────────────────────────────────────────────
// Deps
// ─────────────────────────────────────────────

export interface TokenVaultDeps {
  redis: {
    set(key: string, value: string, mode: "EX", ttl: number): Promise<unknown>
    get(key: string): Promise<string | null>
  }
}

// ─────────────────────────────────────────────
// Tipos
// ─────────────────────────────────────────────

export interface TokenEntry {
  token_id:       string
  category:       DataCategory
  original_value: string
  display:        string        // parcial visível — ex: "****1234"
  tenant_id:      string
  created_at:     string
}

export interface GeneratedToken {
  token_id: string
  display:  string
  /** Token formatado para inserção no stream: [{category}:{token_id}:{display}] */
  inline:   string
}

// ─────────────────────────────────────────────
// TokenVault
// ─────────────────────────────────────────────

export class TokenVault {
  constructor(private readonly deps: TokenVaultDeps) {}

  /**
   * Gera um novo token para um dado sensível.
   *
   * @param tenantId   - tenant_id da sessão
   * @param category   - categoria LGPD do dado
   * @param value      - valor original completo
   * @param display    - trecho visível já calculado pelo MaskingService (ex: "****1234")
   * @param ttlSeconds - TTL do token (deve coincidir com o TTL da sessão)
   */
  async generate(
    tenantId:   string,
    category:   DataCategory,
    value:      string,
    display:    string,
    ttlSeconds: number
  ): Promise<GeneratedToken> {
    const token_id  = `tk_${randomBytes(4).toString("hex")}`
    const created_at = new Date().toISOString()

    const entry: TokenEntry = {
      token_id,
      category,
      original_value: value,
      display,
      tenant_id: tenantId,
      created_at,
    }

    await this.deps.redis.set(
      `${tenantId}:token:${token_id}`,
      JSON.stringify(entry),
      "EX",
      ttlSeconds
    )

    return {
      token_id,
      display,
      inline: `[${category}:${token_id}:${display}]`,
    }
  }

  /**
   * Resolve um token_id para o valor original.
   * Retorna null se o token expirou ou não existe.
   *
   * Chamado exclusivamente por MCP Tools autorizadas (ex: customer_identify)
   * — nunca exposto diretamente ao agente.
   */
  async resolve(tenantId: string, tokenId: string): Promise<string | null> {
    const start = Date.now()
    const raw = await this.deps.redis.get(`${tenantId}:token:${tokenId}`)

    // Constant-time response: always wait at least RESOLVE_MIN_MS
    // to prevent timing-based enumeration of valid token IDs.
    const RESOLVE_MIN_MS = 5
    const elapsed = Date.now() - start
    if (elapsed < RESOLVE_MIN_MS) {
      await new Promise<void>(r => setTimeout(r, RESOLVE_MIN_MS - elapsed))
    }

    if (!raw) return null
    try {
      const entry = JSON.parse(raw) as TokenEntry
      return entry.original_value
    } catch {
      return null
    }
  }

  /**
   * Extrai token_ids de um texto que contém tokens inline.
   * Ex: "seu cpf é [cpf:tk_b7d2e1:***-00] e cartão [credit_card:tk_a8f3:****1234]"
   *     → ["tk_b7d2e1", "tk_a8f3"]
   */
  static extractTokenIds(text: string): string[] {
    const matches = text.matchAll(/\[[\w_]+:(tk_[a-f0-9]+):[^\]]+\]/g)
    return Array.from(matches, m => m[1]).filter((id): id is string => id !== undefined)
  }

  /**
   * Reconstrói o texto com valores originais — usado apenas em paths autorizados
   * (evaluator lendo original_content, auditoria LGPD).
   * Não substitui diretamente de tokens inline — usa original_content do stream.
   */
  static TOKEN_PATTERN = /\[([\w_]+):(tk_[a-f0-9]+):([^\]]+)\]/g
}
