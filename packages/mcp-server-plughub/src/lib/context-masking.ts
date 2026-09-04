/**
 * lib/context-masking.ts — política de máscara do ContextStore, em UM lugar.
 *
 * ── Por que este arquivo existe (arco ALLOWLIST, fatia da SEGUNDA PORTA) ──────
 *
 * O ContextStore tem QUATRO consumidores, e até 2026-08-26 a política vivia
 * inteira dentro de `server.ts`, alcançável só por quem estivesse no mesmo
 * arquivo. Consequência medida (ADR `adr-contextstore-allowlist.md` §1.5): o tool
 * MCP `supervisor_state` (`tools/supervisor.ts`) montava o seu `context_snapshot`
 * fazendo `JSON.parse` do hash **cru** — sem máscara nenhuma — enquanto o endpoint
 * HTTP homônimo, a três arquivos de distância, aplicava a política completa.
 *
 * É exatamente a duplicação que mordeu a leitura de SENTIMENTO em 2026-08-25 (duas
 * implementações independentes e idênticas, só uma consertada, e a que desenhava a
 * tela era a outra), agora sobre PII. O conserto tem a mesma forma que aquele:
 * **uma casa só**, importada pelas duas portas — o padrão de
 * `lib/session-sentiment.ts`.
 *
 * ── O que mora aqui, e o que NÃO mora ────────────────────────────────────────
 *
 * Mora: a resolução da config, o casamento de regra (tag × papel) e as duas
 * máscaras de VALOR.
 *
 * NÃO mora: `applyContextMaskingDynamic`, que fica em `server.ts` porque carrega
 * uma segunda responsabilidade — o **portão de namespace do pool**, que é concern
 * de EXIBIÇÃO ao operador humano e tem exatamente um consumidor (o endpoint que a
 * Console chama). Trazê-la para cá convidaria o próximo call site a aplicar o
 * portão onde não há operador. Ver ADR §D4: são quatro políticas, um vocabulário.
 */

import { MaskingService } from "./masking"
import { observeContextTags } from "./context-map"
import type { ContextMaskingConfig, ContextMaskingRule, ContextMaskingType } from "@plughub/schemas"

// ── ContextMaskingConfig in-process cache ─────────────────────────────────────
// TTL 60s — short enough to pick up Config API changes, long enough to be safe
// under polling loads. Keyed by tenantId.
//
// ⚠️ O cache é do MÓDULO, e isso agora é uma PROPRIEDADE, não um detalhe: as duas
// portas (endpoint HTTP e tool MCP) rodam no MESMO processo e passam a compartilhar
// uma entrada por tenant. Antes o tool não tinha cache porque não tinha política.
const CONTEXT_MASKING_CACHE_TTL_MS = 60_000
interface CachedMaskingConfig { config: ContextMaskingConfig; expiresAt: number }
const contextMaskingConfigCache = new Map<string, CachedMaskingConfig>()

/**
 * Invalidate the cached config for a tenant (called on config.changed events).
 *
 * ⚠️ **Medido em 2026-08-26: esta função não tem NENHUM call site no repositório.**
 * O comentário promete um consumidor de `config.changed` que não existe — a mesma
 * família de promessa-sem-produtor que a § Postura de Engenharia cataloga. Não foi
 * removida porque a assinatura é o gancho certo para quando o consumidor existir;
 * fica registrada no `TODO.md` para que "existe" não seja lido como "está ligada".
 * Enquanto não houver, a janela real de propagação é o TTL de 60 s acima — e é ele
 * que faz uma medição feita logo após a escrita ler a política ANTIGA.
 */
export function invalidateContextMaskingCache(tenantId: string): void {
  contextMaskingConfigCache.delete(tenantId)
}

// config-http-propagation arc: masking config comes from the Config API HTTP
// endpoint (not direct Redis reads). The 60s TTL cache above bounds fetch load.
const CONFIG_API_URL = process.env["CONFIG_API_URL"] ?? "http://localhost:3600"

/**
 * Resolve the ContextMaskingConfig for a tenant, with in-process TTL cache.
 * Delegates to MaskingService.loadContextMaskingConfig() (Config API HTTP) on miss.
 */
export async function getContextMaskingConfig(
  tenantId: string,
): Promise<ContextMaskingConfig> {
  const cached = contextMaskingConfigCache.get(tenantId)
  if (cached && cached.expiresAt > Date.now()) return cached.config
  const config = await MaskingService.loadContextMaskingConfig(CONFIG_API_URL, tenantId)
  contextMaskingConfigCache.set(tenantId, { config, expiresAt: Date.now() + CONTEXT_MASKING_CACHE_TTL_MS })
  return config
}

// ── Pattern matching (specificity algorithm) ──────────────────────────────────

/**
 * Compute specificity score for a rule matching a given tag × role pair.
 *
 * Returns null if the rule does not match. Higher score = more specific.
 *
 * Pattern specificity:
 *   exact match    → 20
 *   suffix glob    → 15 + (segmentos-1)   ("*.cpf" matches "session.cpf")
 *   prefix glob    → 10 + (segmentos-1)   ("caller.*" matches "caller.cpf")
 *   wildcard *     →  0  (matches anything)
 *
 * Role specificity added on top:
 *   matches caller's role category exactly → +2
 *   matches "*"                            → +0
 *
 * ── Por que o SUFIXO existe (2026-08-26) ─────────────────────────────────────
 *
 * As regras protegiam por NAMESPACE (`caller.*`, `account.*`), mas a varredura do
 * ContextStore vivo mostrou que o PII cai em `session.*` e `journey.*` — e esses
 * **não podem ter catch-all**: um `session.* → hidden` derruba
 * `core.workflow.dialog_form_id`/`session.decisions` e a tela de aprovação para de
 * renderizar em silêncio (o seed do config-api avisa isso por escrito).
 *
 * A razão é estrutural: o `delegate.context` de um workflow chega na sessão-filha
 * com prefixo `session.`, então **todo campo que um workflow passa adiante nasce
 * desprotegido** e só fica protegido se alguém lembrar de escrever uma regra
 * EXATA. Foi assim que `session.cpf` ficou em claro enquanto `caller.cpf` estava
 * mascarado, e `journey.numero_cartao` enquanto `session.numero_cartao` estava.
 *
 * O sufixo protege por TIPO DE CAMPO em vez de por namespace: `*.cpf` cobre
 * `session.cpf`, `journey.cpf` e o namespace que ninguém previu ainda.
 *
 * ⚠️ **Casamento em FRONTEIRA DE SEGMENTO, nunca substring.** `tag.endsWith("." + s)`
 * — com `includes`, `*.cpf` casaria `session.xcpf`. Regra que casa DEMAIS é tão
 * ruim quanto a que casa de menos; só falha do outro lado, e mascarar demais é
 * invisível (ninguém abre chamado por ver `***`).
 *
 * ⚠️ **A profundidade entra no score** porque sem ela dois globs da mesma família
 * empatam e o desempate vira ORDEM DA LISTA — `*.limite_aprovado` ×
 * `*.finance.limite_aprovado` responderiam conforme quem foi escrito primeiro.
 * Nenhuma regra atual tem profundidade > 1, então isto não move nada hoje; existe
 * para que o dia em que mover, mova de forma declarada.
 */
function ruleSpecificity(
  pattern:      string,
  ruleRole:     "operator" | "supervisor" | "*",
  tag:          string,
  callerCategory: "operator" | "supervisor",
): number | null {
  // Pattern match
  let patternScore: number
  if (pattern === tag) {
    patternScore = 20
  } else if (pattern.startsWith("*.")) {
    // Suffix glob: protege por TIPO DE CAMPO, através de namespaces.
    const suffix = pattern.slice(2)               // "*.cpf" → "cpf"
    if (!suffix || !tag.endsWith(`.${suffix}`)) return null
    patternScore = 15 + (suffix.split(".").length - 1)
  } else if (pattern.endsWith(".*")) {
    const prefix = pattern.slice(0, -2) // "caller.*" → "caller"
    const tagNs  = tag.split(".").slice(0, prefix.split(".").length).join(".")
    if (tagNs !== prefix) return null
    patternScore = 10 + (prefix.split(".").length - 1)
  } else if (pattern === "*") {
    patternScore = 0
  } else {
    // Non-glob pattern that isn't an exact match — no match
    return null
  }

  // Role match
  let roleScore: number
  if (ruleRole === "*") {
    roleScore = 0
  } else if (ruleRole === callerCategory) {
    roleScore = 2
  } else {
    // Rule targets a different role category — skip
    return null
  }

  return patternScore + roleScore
}

/**
 * Find the most specific ContextMaskingRule for a tag × caller role pair.
 * Returns the matching rule, or null when no rule matches.
 */
export function resolveContextMaskingRule(
  tag:    string,
  callerRole: string,
  config: ContextMaskingConfig,
): ContextMaskingRule | null {
  const callerCategory: "operator" | "supervisor" =
    config.supervisor_roles.includes(callerRole) ? "supervisor" : "operator"

  let bestRule: ContextMaskingRule | null = null
  let bestScore = -1

  for (const rule of config.rules) {
    const score = ruleSpecificity(rule.pattern, rule.role, tag, callerCategory)
    if (score === null) continue
    if (score > bestScore) {
      bestScore = score
      bestRule  = rule
    }
  }

  return bestRule
}

// ── Visual type application ───────────────────────────────────────────────────

/**
 * ⚠️ **`applyMaskingTypeToValue` MUDOU DE CASA em 2026-09-04 (CTX-07).**
 *
 * O corpo dela vive agora em `@plughub/schemas` (`ctx-audience.ts`), e este arquivo
 * apenas REEXPORTA — os chamadores daqui continuam intactos.
 *
 * O motivo é topologia, não arrumação: o `skill-flow-engine` passou a decidir QUAL
 * máscara aplicar (`resolveMaskForAudience`, CTX-01) e não conseguia aplicá-la, porque
 * não importa o mcp-server — e não deve, a dependência é na direção contrária. Copiar
 * para lá faria a QUARTA casa de mascaramento do repositório.
 *
 * Reexportar, e não redefinir, é a regra do `CLAUDE.md` (*"never redefine types from
 * `@plughub/schemas` locally"*) — aqui aplicada a comportamento, não só a tipo.
 */
// Importado E reexportado. `export … from` sozinho reexporta mas NAO traz o nome ao
// escopo local, e `maskContextForPersistence` (abaixo) o chama. O compilador pegou —
// e so pegou porque este arquivo LE a funcao: quem apenas a repassasse ficaria verde.
import { applyMaskingTypeToValue } from "@plughub/schemas"
export { applyMaskingTypeToValue }

/**
 * Snapshot do ContextStore em grau OPERATOR, **sem portão de namespace**.
 *
 * ── Dois consumidores, e por que os dois cabem na MESMA função ───────────────
 *
 *  1. **Persistência durável (F5)** — o registro do que a plataforma SABIA quando
 *     decidiu (`server.ts`, endpoint `/internal/context-snapshot`).
 *  2. **Tool MCP `supervisor_state`** (`tools/supervisor.ts`) — a segunda porta do
 *     ADR §1.5, que até 2026-08-26 devolvia o hash CRU.
 *
 * O que os une não é conveniência: é que **nenhum dos dois tem um visualizador com
 * papel**, e é isso que decide as duas propriedades abaixo.
 *
 * ── Por que NÃO é `applyContextMaskingDynamic` (desvio deliberado do ADR) ─────
 *
 * Aquela função faz DUAS coisas que o ADR trata como uma: o **portão de namespace**
 * (concern de EXIBIÇÃO — quais namespaces o pool do operador expõe na aba Contexto)
 * e o **mascaramento de valor** (concern de PII). Só o segundo pertence aqui.
 *
 * Aplicar o portão faria a config de UI de um pool **apagar história**: um pool
 * que estreitasse `operator_namespaces` deletaria entradas do snapshot, em silêncio
 * e para sempre. É o mesmo defeito que o `CLAUDE.md` já registra para o sentimento
 * (*"um pool que estreitasse a lista apagaria o sentimento em silêncio"*), e por isso
 * lá também se lê o hash CRU. O portão continua valendo na LEITURA, por quem exibe.
 *
 * ⚠️ E no caso do TOOL há uma razão a mais para não aplicá-lo: o pool que o tool
 * tem à mão é o de **ENTRADA** (`session:{id}:meta`), não o que atende — fatia C de
 * `docs/guias/session-meta-ownership.md`. Usá-lo aqui seria impor a política de um
 * pool que pode não ter nada a ver com a sessão em curso.
 *
 * ── Grau OPERATOR, para sempre (decisão do dono, 2026-08-26) ─────────────────
 *
 * Masking é função de (tag × papel) e um snapshot não tem papel. Persistimos no grau
 * mais restritivo: `caller.cpf` vira `***-00` no registro, e **nem supervisor
 * recupera o valor real por aqui**. Alinhado à minimização LGPD e ao caso de uso
 * declarado (*"o que a plataforma SABIA quando decidiu"*).
 *
 * ⚠️ **Consequência a não esquecer: este snapshot NÃO serve a auditoria que precise
 * do valor real.** Essa continua sendo o `TokenVault` de mensagens, que existe para
 * isso e tem controle de acesso próprio. Persistir em grau supervisor seria recriar
 * o cofre que a R7 recusou, com outro nome.
 *
 * ── `hidden` é CONTADO, não omitido ─────────────────────────────────────────
 *
 * Regra de exibição do ADR §3, aplicada já no armazenamento: a entrada fica na
 * linha com `value: null` e `category: "hidden"`. Dropar a chave faria o leitor
 * concluir que a chamada nunca escreveu nada — ausência plausível de novo.
 *
 * `agent.*` sai (visibilidade por participante, resolvida noutro lugar) e **não
 * entra no total**: não é fato do contato, então contá-lo como "oculto" mentiria
 * sobre quantas entradas o operador deixou de ver.
 */
export interface PersistenceSnapshot {
  entries:      Record<string, unknown>
  total:        number
  hidden_count: number
}

export async function maskContextForPersistence(
  rawHash:  Record<string, string>,
  tenantId: string,
): Promise<PersistenceSnapshot> {
  const config = await getContextMaskingConfig(tenantId)

  // ── V3 — modo AUDITORIA (não altera NADA abaixo) ────────────────────────────
  // A SEGUNDA porta. Instrumentar só a da Console deixaria esta medindo nada, sem
  // nada ficar vermelho — que é exatamente como o `supervisor_state` passou meses
  // devolvendo o hash cru (ADR §1.5).
  void observeContextTags(rawHash, tenantId).catch(() => { /* já logado na casa */ })

  const entries: Record<string, unknown> = {}
  let total  = 0
  let hidden = 0

  for (const [tag, raw] of Object.entries(rawHash)) {
    const ns = tag.split(".")[0] ?? ""
    if (ns === "agent") continue
    total++

    let entry: Record<string, unknown>
    try { entry = JSON.parse(raw) as Record<string, unknown> }
    catch { entry = { value: raw } }

    const rule     = resolveContextMaskingRule(tag, "operator", config)
    const maskType = rule ? rule.type : config.default_unmatched_operator

    if (maskType === "hidden") {
      hidden++
      entries[tag] = { ...entry, value: null, masked: true, category: "hidden" }
      continue
    }
    if (maskType === "plain") {
      entries[tag] = entry
      continue
    }
    entries[tag] = {
      ...entry,
      value:    applyMaskingTypeToValue(String(entry["value"] ?? ""), maskType),
      pii:      true,
      masked:   true,
      category: maskType,
    }
  }

  return { entries, total, hidden_count: hidden }
}
