/**
 * lib/endpoint-token.ts
 * Geração e verificação de token de endpoint de canal.
 *
 * ── Procedência do desenho ────────────────────────────────────────────────────
 * Isto é a PORTA (TypeScript) de `workflow-api/webhooks.py`, o único caminho
 * autenticado que a plataforma tinha antes de 2026-08-07. A Fase F do ADR
 * `adr-webhook-endpoint-single-registry` decidiu aposentar aquele registro legado —
 * mas o mecanismo de token dele estava CERTO; o que estava morto era o ciclo de vida
 * de instância a que ele vinha grudado. Portar em vez de reinventar é a decisão:
 * o desenho já foi revisado, testado e usado.
 *
 * Propriedades preservadas do original, todas deliberadas:
 *   · Prefixo `plughub_wh_` — torna o token reconhecível em log e em scanner de
 *     credencial (GitHub secret scanning e afins procuram prefixos estáveis).
 *   · 32 bytes de CSPRNG (`randomBytes`) → ~256 bits. Não é UUID: UUID v4 tem 122
 *     bits e formato previsível.
 *   · Persistência só do SHA-256. O token em claro é devolvido UMA vez e nunca mais;
 *     não há "recuperar token", só rotacionar.
 *   · Comparação em TEMPO CONSTANTE (`timingSafeEqual`) — `===` sobre hash vaza, por
 *     tempo, quantos caracteres iniciais bateram.
 *   · `token_prefix` (16 chars) guardado para IDENTIFICAR o token na tela e no log
 *     sem dar material de busca: 16 de 256 bits não estreitam nada de útil.
 *
 * Diferença em relação ao original: SHA-256 e não bcrypt/argon2 — de propósito. O
 * segredo é de ALTA ENTROPIA e gerado por nós, não escolhido por humano, então não há
 * dicionário a resistir; o custo de KDF só adicionaria latência no caminho quente da
 * verificação. Para senha de usuário a escolha seria a oposta.
 */

import { createHash, randomBytes, timingSafeEqual } from "crypto"

/** Prefixo estável — reconhecível em logs e scanners de credencial. */
const TOKEN_PREFIX = "plughub_wh_"

/** Quantos caracteres do token em claro ficam guardados para exibição. */
const PREFIX_DISPLAY_LEN = 16

export interface GeneratedToken {
  /** Token completo — mostrado UMA vez ao operador, nunca persistido. */
  plain:  string
  /** SHA-256 hex — o que vai para o banco. */
  hash:   string
  /** Primeiros 16 chars do plain — identificação na tela/log. */
  prefix: string
}

/** Gera um token novo. O `plain` é a única cópia; perdeu, rotaciona. */
export function generateEndpointToken(): GeneratedToken {
  const plain = `${TOKEN_PREFIX}${randomBytes(32).toString("base64url")}`
  return {
    plain,
    hash:   hashEndpointToken(plain),
    prefix: plain.slice(0, PREFIX_DISPLAY_LEN),
  }
}

export function hashEndpointToken(plain: string): string {
  return createHash("sha256").update(plain, "utf8").digest("hex")
}

/**
 * Verifica um token candidato contra o hash guardado, em tempo constante.
 *
 * `timingSafeEqual` exige buffers do MESMO tamanho, senão lança. Como os dois lados
 * são digests hex de SHA-256 (64 chars sempre), o tamanho só difere se o hash
 * guardado estiver corrompido/truncado — e aí a resposta correta é `false`, não uma
 * exceção que vira 500 e conta ao chamador que aquele endpoint tem token quebrado.
 */
export function verifyEndpointToken(plain: string, storedHash: string): boolean {
  if (!plain || !storedHash) return false
  const a = Buffer.from(hashEndpointToken(plain), "utf8")
  const b = Buffer.from(storedHash, "utf8")
  if (a.length !== b.length) return false
  return timingSafeEqual(a, b)
}
