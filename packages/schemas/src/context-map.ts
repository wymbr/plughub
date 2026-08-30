/**
 * context-map.ts — o MAPA do ContextStore (D2 do arco ALLOWLIST, fase V3).
 *
 * ── O que este arquivo é, e o que ele ainda NÃO faz ──────────────────────────
 *
 * O mapa é a **allowlist**: a declaração de quais campos existem no ContextStore,
 * em `escopo.dominio.campo`, cada folha nomeando o seu **tipo** do catálogo
 * (`masking.types`, fase V2). Não há uma segunda lista.
 *
 * Na V3 o mapa **não recusa nada**. Ele é lido em **modo auditoria**: o runtime
 * classifica cada tag observada em declarada × não-declarada × dinâmica e CONTA,
 * sem esconder nem barrar. É essa contagem que produz a lista real com que a V4
 * decide inverter para deny-by-default — e é por isso que o enum `mode` tem um
 * valor só. Não existe config capaz de ligar imposição antes de o código existir:
 * a chave que a V4 vira não pode estar ao alcance de quem edita o JSON hoje.
 *
 * ── Por que o ESCOPO é o primeiro segmento (D2, recusa medida) ───────────────
 *
 * O primeiro segmento decide **hash e TTL** hoje, em três casas independentes
 * (`sdk/src/context-store.ts:106-120`, `skill-flow-engine/src/interpolate.ts:237`,
 * `mcp-server-plughub/src/tools/journey.ts:180`). A alternativa — raiz por domínio
 * de negócio, escopo declarado no nó — obrigaria todo escritor a ter o mapa
 * carregado para saber em qual hash gravar, e os escritores estão em TypeScript
 * **e** em Python. Seria roteamento de RETENÇÃO DE PII dependente de config, na
 * casa em que o `CLAUDE.md` já registra três causas empilhadas de leitura de config
 * falhando, todas degradando para *"usa o default"*. Dado pessoal num hash de 90
 * dias porque a config não carregou é o modo de falha que essa recusa evita.
 *
 * ── `legado` é ARRAY, e isso é medição, não gosto (emenda à D2) ──────────────
 *
 * O exemplo da D2 traz `legado: "caller.cpf"`, no singular. A varredura de
 * 2026-08-29 mediu **duas grafias vivas para o mesmo campo** — `caller.cpf` (escrito
 * por `agente_contexto_ia_v1.yaml:111,230`) e `session.cpf` (depositado pelo
 * `delegate.context` de workflow, achado na varredura de 08-26 que motivou os globs
 * de sufixo). Com `legado` escalar restariam duas saídas, ambas ruins: dois nós
 * canônicos para um campo — que quebra a D3.2 (*"só a canônica é armazenada"*), já
 * que passariam a existir duas canônicas —, ou descartar um alias em silêncio, que
 * é o vazamento que o arco existe para matar.
 */

import { z } from "zod"
import { DEFAULT_DATA_TYPE_CATALOG, type DataTypeCatalog } from "./audit"

/**
 * Escopos válidos — e a lista é FECHADA porque cada valor corresponde a um
 * roteamento de storage que já existe no código (§1.6 do ADR):
 *
 *   session  → `{t}:ctx:{sessionId}`          TTL   4 h   (default)
 *   journey  → `{t}:ctx:journey:{raiz}`       TTL  30 d   (prefixo `journey.`)
 *   customer → `{t}:ctx:customer:{customerId}` TTL 90 d   (`insight.historico`, `pricing`)
 *
 * Um escopo novo aqui sem o roteamento correspondente declararia uma retenção que
 * ninguém aplica — a família "promessa sem produtor" que o `CLAUDE.md` cataloga.
 */
export const ContextScopeSchema = z.enum(["session", "journey", "customer"])
export type ContextScope = z.infer<typeof ContextScopeSchema>

/** Folha do mapa: um campo declarado. */
export const ContextMapFieldSchema = z.object({
  /** id de um tipo do catálogo `masking.types`. Conferido pelo oráculo. */
  tipo: z.string().min(1),
  /**
   * Grafias legadas que resolvem para esta canônica, na BORDA (D3.1).
   * Nenhuma regra, nenhum nó, nenhuma allowlist é escrita contra um alias.
   *
   * `.optional()` e não `.default([])` pelo mesmo motivo de `declared_only` em
   * `audit.ts`: com `default`, o campo vira OBRIGATÓRIO no tipo de SAÍDA do Zod e
   * as ~40 folhas já canônicas do mapa teriam de repetir `legado: []` sem
   * acrescentar nada. Ausente já significa "sem alias" para os consumidores, que
   * leem `leaf.legado ?? []`.
   */
  legado: z.array(z.string().min(1)).optional(),
  label: z.string().optional(),
})
export type ContextMapField = z.infer<typeof ContextMapFieldSchema>

export const ContextMapSchema = z.object({
  /**
   * UM valor, de propósito — ver o cabeçalho. A V4 acrescenta `"enforce"` junto do
   * código que o honra; até lá, uma config que traga qualquer outro valor é
   * rejeitada pelo Zod e cai no default, que é o modo que não esconde nada.
   */
  mode: z.enum(["audit"]).default("audit"),
  /**
   * Prefixos cujo segundo segmento é um ID em runtime — `agent.{participantId}.*`
   * e `segment.{segId}.*`. Não são declaráveis campo a campo e por isso formam um
   * TERCEIRO balde na auditoria, nunca "não declarado".
   *
   * Somá-los aos não-declarados inflaria com campos impossíveis de declarar
   * justamente o número que autoriza a V4 — medir exposição e chamá-la de dano.
   */
  dynamic_prefixes: z.array(z.string().min(1)).default(["agent.", "segment."]),
  /** escopo → domínio → campo → folha */
  contexto: z.record(z.record(z.record(ContextMapFieldSchema))).default({}),
})
export type ContextMap = z.infer<typeof ContextMapSchema>

// ─────────────────────────────────────────────
// DEFAULT_CONTEXT_MAP — semeado no config-api (`masking.context_map`)
// ─────────────────────────────────────────────

/**
 * O mapa global semeado, construído a partir do CENSO de 2026-08-29 — não de
 * imaginação. Duas varreduras, porque leitura e escrita não coincidem:
 *
 *   · LEITURAS  — 231 ocorrências de `@ctx.<ns>.<campo>` em 53 arquivos de
 *     `packages/`. (O `486` da §1.8 do ADR **não se reproduz**: foi medido por
 *     outro critério, provavelmente contando `@ctx.` ∪ `:ctx:` sobre `docs/`
 *     junto. O denominador do mapa é este.)
 *   · ESCRITAS  — as declarações `tag:` dos `context_tags` nos YAML de skill, que
 *     trazem campos que leitura nenhuma menciona (`caller.telefone`,
 *     `caller.intencao_primaria`, `session.wrapup.*`, `account.status`).
 *
 * ── Critério de SEMEADURA: só o que é tipável com confiança ──────────────────
 *
 * Campo medido cujo tipo não existe no catálogo **fica de fora**, de propósito.
 * `session.vencimento_cartao` é o caso: as regras vivas o mascaram (`last_2`) e
 * nenhum tipo do catálogo serve — `credit_card` é `last_4` e num `MM/AA` isso
 * mostraria quase tudo, que é o mesmo argumento pelo qual a T6 recusou reusar
 * `credit_card` para o CVV.
 *
 * Declarar um tipo aproximado ali seria escrever no mapa uma política que ninguém
 * decidiu, e a V4 a aplicaria. Deixar de fora faz a **auditoria acusar o campo**,
 * que é o comportamento pretendido: o mapa é medido pelo que falta nele, e a V4
 * não pode virar a chave enquanto a lista não fechar. A lacuna é do CATÁLOGO, e
 * fecha-se lá.
 *
 * ── Achado de exposição que o censo produziu (dano a medir, não presumir) ────
 *
 * `session.numero_atual` guarda o **telefone** do cliente (linha atual, fluxo de
 * portabilidade — `agente_portabilidade_intake_v1.yaml:445`, `confidence: 1.0`) e
 * **não casa nenhuma das 23 regras** do tenant: `*.telefone` exige sufixo
 * `.telefone`, e não há catch-all de `session.*` (não pode haver — derrubaria a
 * tela de aprovação). Cai no `default_unmatched_operator: "plain"` e é exibido em
 * CLARO ao operador. É a §1.1 do ADR acontecendo num campo concreto. Aqui ele é
 * declarado `phone`; o conserto durável é a V4.
 */
export const DEFAULT_CONTEXT_MAP: ContextMap = {
  mode:             "audit",
  dynamic_prefixes: ["agent.", "segment."],
  contexto: {
    session: {
      // ── Dados do cliente — hoje no namespace `caller.*` ──────────────────
      cliente: {
        nome:              { tipo: "texto",      legado: ["caller.nome"] },
        cpf:               { tipo: "cpf",        legado: ["caller.cpf", "session.cpf"] },
        telefone:          { tipo: "phone",      legado: ["caller.telefone"] },
        email:             { tipo: "email_addr", legado: ["caller.email"] },
        customer_id:       { tipo: "texto",      legado: ["caller.customer_id", "session.customer_id"],
                             label: "ID interno — não-PII, necessário p/ histórico/360" },
        account_id:        { tipo: "texto",      legado: ["caller.account_id"] },
        motivo_contato:    { tipo: "texto",      legado: ["caller.motivo_contato"] },
        intencao_primaria: { tipo: "texto",      legado: ["caller.intencao_primaria"] },
        sentimento_atual:  { tipo: "texto",      legado: ["caller.sentimento_atual"] },
      },
      // ── Dados da conta — hoje no namespace `account.*` ───────────────────
      conta: {
        plano_atual: { tipo: "texto", legado: ["account.plano_atual", "caller.plano_atual"] },
        status:      { tipo: "texto", legado: ["account.status"] },
      },
      // ── Pacote de aprovação (aumento de limite) — hoje achatado em `session.*` ──
      cartao: {
        numero:            { tipo: "credit_card", legado: ["session.numero_cartao"] },
        cpf_titular:       { tipo: "cpf",         legado: ["session.cpf_titular"] },
        limite_solicitado: { tipo: "financial",   legado: ["session.limite_solicitado"] },
        limite_aprovado:   { tipo: "financial",   legado: ["session.limite_aprovado"] },
      },
      // ── Já CANÔNICOS: escritos em `escopo.dominio.campo` pelo routing-engine ──
      // Nenhum `legado`, e é isso que dá ao contador do D3 o seu par: sem tag
      // canônica viva, "ninguém migrou" e "ninguém usa" seriam indistinguíveis.
      pool: {
        id:                { tipo: "texto" },
        channels:          { tipo: "texto" },
        llm_account_ids:   { tipo: "texto" },
        max_reply_time_ms: { tipo: "texto" },
        mentionable_pools: { tipo: "texto" },
      },
      queue: {
        position: { tipo: "texto" },
        eta_ms:   { tipo: "texto" },
      },
      sentimento: {
        current:   { tipo: "texto" },
        categoria: { tipo: "texto", label: "Classificada na LEITURA — sem produtor próprio" },
      },
      wrapup: {
        resumo:            { tipo: "texto" },
        classificacao:     { tipo: "texto" },
        escalation_reason: { tipo: "texto" },
        proximos_passos:   { tipo: "texto" },
      },
      // ── Encanamento de workflow ──────────────────────────────────────────
      // Os dois tokens são `credential`: são capacidades ao portador, com a mesma
      // POLÍTICA (`operator: hidden`) e a mesma CLASSE do tipo. O catálogo declara
      // que dois campos com política e classe iguais são UM tipo — inventar um
      // `token` seria o oitavo inventário.
      workflow: {
        dialog_form_id:        { tipo: "texto" },
        resume_token:          { tipo: "credential", legado: ["session.workflow_resume_token"] },
        delegate_resume_token: { tipo: "credential", legado: ["session.delegate_resume_token"] },
        current_round:         { tipo: "texto" },
        max_rounds:            { tipo: "texto" },
        decisions:             { tipo: "texto" },
        origin_session_id:     { tipo: "texto" },
        briefing_session_id:   { tipo: "texto" },
      },
      contato: {
        close_origin:               { tipo: "texto" },
        contact_channel:            { tipo: "texto" },
        contact_identifier:         { tipo: "texto" },
        contact_outcome:            { tipo: "texto" },
        customer_present:           { tipo: "texto" },
        customer_participant_id:    { tipo: "texto" },
        human_agent_participant_id: { tipo: "texto" },
        confirmation_channel:       { tipo: "texto" },
      },
      survey: {
        form_id:             { tipo: "texto" },
        grain:               { tipo: "texto" },
        origin:              { tipo: "texto" },
        origin_pool:         { tipo: "texto" },
        pool_id:             { tipo: "texto" },
        segment_id:          { tipo: "texto" },
        target_id:           { tipo: "texto" },
        customer_key:        { tipo: "texto" },
        agent_key:           { tipo: "texto" },
        surveyed_agent_key:  { tipo: "texto" },
        surveyed_segment_id: { tipo: "texto" },
      },
      portabilidade: {
        // Telefone. Ver o achado de exposição no cabeçalho.
        numero_atual:      { tipo: "phone", legado: ["session.numero_atual"] },
        operadora_destino: { tipo: "texto", legado: ["session.operadora_destino"] },
      },
      reembolso: {
        numero_pedido:    { tipo: "texto", legado: ["session.numero_pedido"] },
        motivo_reembolso: { tipo: "texto", legado: ["session.motivo_reembolso"] },
      },
      deploy: {
        notes:       { tipo: "texto", legado: ["session.deploy_notes"] },
        deployed_by: { tipo: "texto", legado: ["session.deployed_by"] },
        skill_id:    { tipo: "texto", legado: ["session.skill_id"] },
      },
      campanha: {
        campaign_id: { tipo: "texto", legado: ["session.campaign_id"] },
        delivery_id: { tipo: "texto", legado: ["session.delivery_id"] },
      },
      hook: {
        wrapup_pool:       { tipo: "texto", legado: ["hook.wrapup_pool"] },
        dialog_form_id:    { tipo: "texto", legado: ["hook.dialog_form_id"] },
        acw_timeout_hours: { tipo: "texto", legado: ["hook.acw_timeout_hours"] },
      },
    },
    journey: {
      processo: {
        resultado:              { tipo: "texto", legado: ["journey.resultado"] },
        parecer:                { tipo: "texto", legado: ["journey.parecer"] },
        numero_pedido:          { tipo: "texto", legado: ["journey.numero_pedido"] },
        pedido:                 { tipo: "texto", legado: ["journey.pedido"] },
        origin_process_session: { tipo: "texto", legado: ["journey.origin_process_session"] },
      },
      // PII no hash de 30 DIAS — o custo de errar aqui é 180× o de errar na sessão.
      cartao: {
        numero:          { tipo: "credit_card", legado: ["journey.numero_cartao"] },
        limite_aprovado: { tipo: "financial",   legado: ["journey.limite_aprovado"] },
      },
    },
    // `customer` (`{t}:ctx:customer:{id}`, 90 d) tem roteamento vivo
    // (`LONG_TTL_PREFIXES = ["insight.historico", "pricing"]`) e **zero ocorrências
    // no censo**. Fica FORA do mapa de propósito: declarar campo que ninguém
    // escreve é o modo de falha barulhento da allowlist, e o silencioso é o
    // inverso. Se algum dia alguém escrever ali, a auditoria acusa.
  },
}

// ─────────────────────────────────────────────
// Índice de resolução — UMA casa, três consumidores
// ─────────────────────────────────────────────

/**
 * Como uma tag observada é classificada. Quatro valores, e o quarto existe para
 * não inflar o terceiro:
 *
 *   · `canonical` — a tag já é `escopo.dominio.campo` e está no mapa
 *   · `alias`     — a tag é uma grafia legada declarada em algum nó (D3)
 *   · `dynamic`   — prefixo com ID em runtime (`agent.`, `segment.`); NÃO é
 *                   declarável campo a campo, logo não é "não declarada"
 *   · `unknown`   — não está no mapa. É ESTA a população que a V4 precisa contar.
 */
export type ContextTagOrigin = "canonical" | "alias" | "dynamic" | "unknown"

export interface ContextTagResolution {
  /** Nome canônico quando conhecido; a própria tag quando `dynamic`/`unknown`. */
  canonical: string
  origin:    ContextTagOrigin
  /** Tipo declarado — só existe para `canonical`/`alias`. */
  tipo?:     string
}

export interface ContextTagIndex {
  /** canônica → tipo */
  canonical: Map<string, string>
  /** grafia legada → canônica */
  alias:     Map<string, string>
  dynamicPrefixes: string[]
}

/**
 * Constrói o índice de resolução a partir do mapa.
 *
 * Existe como função exportada — e não inline em cada consumidor — porque os três
 * lugares que precisam dela (o oráculo, o runtime do mcp-server e o gate) fariam
 * três leituras da mesma árvore, e é assim que as cópias divergem. O `CLAUDE.md`
 * registra o caso do sentimento, em que duas implementações idênticas existiram e
 * só a que NÃO desenhava a tela foi consertada.
 */
export function buildContextTagIndex(map: ContextMap): ContextTagIndex {
  const canonical = new Map<string, string>()
  const alias     = new Map<string, string>()

  for (const [escopo, dominios] of Object.entries(map.contexto)) {
    for (const [dominio, campos] of Object.entries(dominios)) {
      for (const [campo, leaf] of Object.entries(campos)) {
        const name = `${escopo}.${dominio}.${campo}`
        canonical.set(name, leaf.tipo)
        for (const old of leaf.legado ?? []) alias.set(old, name)
      }
    }
  }
  return { canonical, alias, dynamicPrefixes: map.dynamic_prefixes }
}

/**
 * Resolve uma tag observada — **na BORDA, antes de qualquer decisão de política**
 * (D3.1). Nenhuma regra é escrita contra alias; quem chama já recebe a canônica.
 *
 * A ordem é deliberada: canônica → alias → dinâmica → desconhecida. Consultar o
 * alias primeiro deixaria uma canônica ser sombreada por uma grafia legada que
 * outro nó reivindicasse — e é justamente isso que o oráculo proíbe declarar.
 */
export function resolveContextTag(tag: string, index: ContextTagIndex): ContextTagResolution {
  const tipo = index.canonical.get(tag)
  if (tipo !== undefined) return { canonical: tag, origin: "canonical", tipo }

  const canon = index.alias.get(tag)
  if (canon !== undefined) {
    const t = index.canonical.get(canon)
    return t !== undefined
      ? { canonical: canon, origin: "alias", tipo: t }
      : { canonical: canon, origin: "alias" }
  }

  if (index.dynamicPrefixes.some(p => tag.startsWith(p))) {
    return { canonical: tag, origin: "dynamic" }
  }
  return { canonical: tag, origin: "unknown" }
}

// ─────────────────────────────────────────────
// Oráculo do mapa
// ─────────────────────────────────────────────

export interface ContextMapVerification {
  /** Testemunha de PRESENÇA: um mapa vazio não pode passar por ser vazio. */
  declared:        number
  aliases:         number
  /** `tipo` que o catálogo não declara. */
  unknown_types:           Array<{ field: string; tipo: string }>
  /** Escopo fora do enum — declararia uma retenção que nenhum roteamento aplica. */
  unknown_scopes:          string[]
  /** Mesma grafia legada reivindicada por dois nós: a resolução dependeria da ordem. */
  ambiguous_aliases:       Array<{ legado: string; claimed_by: string[] }>
  /** Grafia legada que também é canônica de outro nó: o alias sombrearia um campo real. */
  alias_shadows_canonical: Array<{ legado: string; canonical_of: string }>
}

/**
 * verifyContextMap — o ORÁCULO do gate da V3, exportado pelo mesmo motivo que
 * `verifyDataTypeCatalog`: um gate que reconstrói a regra que julga testa a si
 * mesmo.
 *
 * ── Por que NÃO há o lado "todo tipo do catálogo é usado por algum campo" ────
 *
 * O oráculo da V2 exige os dois lados porque lá as duas listas descrevem a MESMA
 * população (tipo × categoria detectável). Aqui não: `opaque`, `card_cvv` e
 * `credential` são alcançáveis por `masked:` numa declaração de formulário, que
 * não é campo de ContextStore. Exigir reciprocidade reprovaria o catálogo por
 * conter tipos que este mapa, por construção, não usa — mediria a proposição
 * vizinha. A completude do catálogo continua sendo de `verifyDataTypeCatalog`.
 *
 * O segundo lado que a V3 precisa é OUTRO, e vive no runtime: o par de contadores
 * alias × canônica (ADR §7), sem o qual "ninguém migrou" e "ninguém usa" ficam
 * indistinguíveis.
 */
export function verifyContextMap(
  map:     ContextMap    = DEFAULT_CONTEXT_MAP,
  catalog: DataTypeCatalog = DEFAULT_DATA_TYPE_CATALOG,
): ContextMapVerification {
  const typeIds = new Set(catalog.types.map(t => t.id))
  const index   = buildContextTagIndex(map)

  const unknown_types:  Array<{ field: string; tipo: string }> = []
  const unknown_scopes: string[] = []
  const claims = new Map<string, string[]>()   // legado → canônicas que o reivindicam

  for (const [escopo, dominios] of Object.entries(map.contexto)) {
    if (!ContextScopeSchema.safeParse(escopo).success) unknown_scopes.push(escopo)
    for (const [dominio, campos] of Object.entries(dominios)) {
      for (const [campo, leaf] of Object.entries(campos)) {
        const name = `${escopo}.${dominio}.${campo}`
        if (!typeIds.has(leaf.tipo)) unknown_types.push({ field: name, tipo: leaf.tipo })
        for (const old of leaf.legado ?? []) {
          claims.set(old, [...(claims.get(old) ?? []), name])
        }
      }
    }
  }

  const ambiguous_aliases = [...claims.entries()]
    .filter(([, owners]) => owners.length > 1)
    .map(([legado, claimed_by]) => ({ legado, claimed_by }))

  const alias_shadows_canonical = [...claims.keys()]
    .filter(legado => index.canonical.has(legado))
    .map(legado => ({ legado, canonical_of: legado }))

  return {
    declared: index.canonical.size,
    aliases:  index.alias.size,
    unknown_types,
    unknown_scopes,
    ambiguous_aliases,
    alias_shadows_canonical,
  }
}

// ─────────────────────────────────────────────
// Opções de `context_visibility` (D6 / fase V5)
// ─────────────────────────────────────────────

/**
 * O que a tela do pool pode OFERECER, derivado do mapa.
 *
 * ── Por que isto vive aqui, e não no platform-ui ─────────────────────────────
 *
 * O `platform-ui` **não depende de `@plughub/schemas`** — redefine os contratos à
 * mão em `types/index.ts`, dívida que o `CLAUDE.md` registra. Derivar a lista lá
 * criaria uma SEGUNDA leitura da árvore do mapa, e é assim que dois vocabulários
 * nascem no arco que existe para colapsar sete. A derivação fica na casa do mapa;
 * quem serve a tela é um endpoint que a chama.
 *
 * ── `agent.*` fica FORA das opções, e isso é medido ─────────────────────────
 *
 * O portão de namespace (`applyContextMaskingDynamic`) descarta `agent.*` **antes**
 * de consultar a lista (`if (ns === "agent") continue`). Um pool que declarasse
 * `agent` não veria nada: a opção seria inerte por construção. Oferecê-la é a mesma
 * família do `service` — item de menu que não faz nada, e que o operador só
 * descobre inerte quando o campo não aparece.
 */
export interface ContextVisibilityNamespaceOption {
  ns: string
  /**
   * `canonical` — o namespace é ESCOPO no modelo novo (`session`, `journey`).
   * `legacy`    — só existe como primeiro segmento de alias (`caller`, `account`,
   *               `hook`); some quando a migração terminar.
   */
  source: "canonical" | "legacy"
  /** Quantos campos do mapa caem neste namespace — dá peso à escolha. */
  fields: number
}

export interface ContextVisibilityTagOption {
  /** A grafia selecionável (canônica ou legada) — é ela que vai para a config. */
  tag:       string
  canonical: string
  tipo:      string
  origin:    "canonical" | "alias"
}

export interface ContextVisibilityOptions {
  namespaces: ContextVisibilityNamespaceOption[]
  tags:       ContextVisibilityTagOption[]
}

/**
 * Deriva as opções do mapa. **Nenhuma lista literal** — é o mecanismo que a D6
 * pede: não há como escolher um namespace que não existe, porque a lista não é
 * escrita, é medida.
 *
 * As grafias LEGADAS entram de propósito: enquanto a migração não termina, o
 * ContextStore vivo guarda `caller.cpf`, e uma lista só com canônicas ofereceria
 * exatamente o que o portão NÃO vai casar hoje.
 */
export function contextVisibilityOptions(map: ContextMap = DEFAULT_CONTEXT_MAP): ContextVisibilityOptions {
  const index = buildContextTagIndex(map)
  const nsCount = new Map<string, { canonical: number; legacy: number }>()
  const bump = (name: string, kind: "canonical" | "legacy") => {
    const ns  = name.split(".")[0] ?? ""
    if (!ns || ns === "agent") return
    const cur = nsCount.get(ns) ?? { canonical: 0, legacy: 0 }
    cur[kind]++
    nsCount.set(ns, cur)
  }

  const tags: ContextVisibilityTagOption[] = []
  for (const [canonical, tipo] of index.canonical) {
    bump(canonical, "canonical")
    tags.push({ tag: canonical, canonical, tipo, origin: "canonical" })
  }
  for (const [legado, canonical] of index.alias) {
    bump(legado, "legacy")
    tags.push({ tag: legado, canonical, tipo: index.canonical.get(canonical) ?? "", origin: "alias" })
  }

  const namespaces: ContextVisibilityNamespaceOption[] = [...nsCount.entries()]
    .map(([ns, c]) => ({
      ns,
      // Um namespace que tem QUALQUER canônica é escopo do modelo novo; os demais
      // só sobrevivem enquanto houver alias apontando para fora deles.
      source: (c.canonical > 0 ? "canonical" : "legacy") as "canonical" | "legacy",
      fields: c.canonical + c.legacy,
    }))
    .sort((a, b) => (a.source === b.source ? b.fields - a.fields : a.source === "canonical" ? -1 : 1))

  tags.sort((a, b) => a.tag.localeCompare(b.tag))
  return { namespaces, tags }
}
