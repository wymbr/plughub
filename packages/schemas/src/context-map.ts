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
 * Onde uma tag é ARMAZENADA, e por quanto tempo. Três destinos, e são fato de
 * CÓDIGO — cada um corresponde a uma chave Redis que já existe:
 *
 *   session  → `{t}:ctx:{sessionId}`           TTL   4 h
 *   journey  → `{t}:ctx:journey:{raiz}`        TTL  30 d
 *   customer → `{t}:ctx:customer:{customerId}` TTL  90 d
 */
export const ContextStoreKindSchema = z.enum(["session", "journey", "customer"])
export type ContextStoreKind = z.infer<typeof ContextStoreKindSchema>

/**
 * A TABELA DE ROTEAMENTO — a declaração ÚNICA de qual prefixo vai para qual store.
 *
 * ── Por que ela mudou de casa (CNS-04, 2026-09-01) ───────────────────────────
 *
 * Até aqui havia DUAS listas para a mesma pergunta: `ContextScopeSchema`
 * (`session|journey|customer`), que o oráculo do mapa usava para aprovar um root, e
 * as constantes de prefixo do `sdk/context-store.ts`, que decidem de verdade o TTL e
 * a chave. **Elas discordavam, e a divergência era uma armadilha ARMADA:** o enum
 * admitia `customer` como root do mapa e o comentário prometia 90 dias, mas nenhuma
 * rota casa o prefixo `customer.` — as rotas de 90 d são `insight.historico`,
 * `pricing` e `core.customer.`. Um `contexto.customer.x` declarado no mapa teria a
 * canônica `customer.x`, que cai no default e vive **4 horas**. Dano zero hoje
 * (nenhum root `customer` no mapa vigente), pela mesma razão que o legado
 * `rule.{category}` da V2b: ausência de dado, não ausência de defeito.
 *
 * Agora existe uma casa só, e ela mora em `schemas` porque é o pacote base — o SDK
 * depende dele, nunca o contrário.
 *
 * ⚠️ **A ordem é significativa.** `core.customer.` tem de ser avaliada antes de
 * qualquer regra mais larga sobre `core.`: com um casador largo, `core.journey.*`
 * casaria a rota de cliente primeiro e receberia 90 d em vez de 30 — foi exatamente
 * o que a bateria de mutação da CNS-03 mediu, e é por isso que a tabela é uma LISTA
 * ORDENADA e não um mapa.
 */
export const CONTEXT_ROUTE_PREFIXES: ReadonlyArray<{ prefix: string; store: ContextStoreKind }> = [
  { prefix: "insight.historico", store: "customer" },
  { prefix: "pricing",           store: "customer" },
  { prefix: "core.customer.",    store: "customer" },
  { prefix: "journey.",          store: "journey"  },
  { prefix: "core.journey.",     store: "journey"  },
]

/**
 * Resolve o store de uma tag. **O default é `session`** — e ele é honesto, não
 * omissão: qualquer root não listado vive no hash da sessão com o TTL da sessão, que
 * é o que o roteamento sempre fez para tudo que não fosse journey/cliente. É o que
 * permite a CNS-02 liberar roots de tenant sem tocar em roteamento nenhum.
 */
export function resolveContextStore(tag: string): ContextStoreKind {
  for (const r of CONTEXT_ROUTE_PREFIXES) if (tag.startsWith(r.prefix)) return r.store
  return "session"
}

/**
 * @deprecated Era o gate de root do oráculo, e gateava a coisa errada — ver o
 * comentário de `CONTEXT_ROUTE_PREFIXES`. Sobrevive só como vocabulário; quem decide
 * onde uma tag mora é `resolveContextStore`.
 */
export const ContextScopeSchema = ContextStoreKindSchema
export type ContextScope = ContextStoreKind

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
  dynamic_prefixes: z.array(z.string().min(1)).default(["agent.", "segment.", "core.segment."]),
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
 * Declarar um tipo aproximado seria escrever no mapa uma política que ninguém
 * decidiu, e a V4 a aplicaria. Deixar de fora faz a **auditoria acusar o campo**,
 * que é o comportamento pretendido: o mapa é medido pelo que falta nele, e a V4
 * não pode virar a chave enquanto a lista não fechar.
 *
 * ✅ **O caso que originou o critério FECHOU em 2026-08-30.**
 * `session.vencimento_cartao` ficou fora da V3 porque nenhum dos 11 tipos servia —
 * `credit_card` é `last_4` e sobre `MM/AA` mostraria quase tudo (o argumento pelo
 * qual a T6 recusou reusá-lo no CVV). A lacuna era do CATÁLOGO e foi fechada LÁ,
 * com o tipo `card_expiry`; só então o campo entrou aqui, como
 * `session.cartao.vencimento`. **A ordem é o critério**: catálogo primeiro, mapa
 * depois — nunca o inverso.
 *
 * ── Achado de exposição que o censo produziu (dano a medir, não presumir) ────
 *
 * `session.numero_atual` guarda um **telefone** (a linha sendo portada — fluxo de
 * portabilidade, `agente_portabilidade_intake_v1.yaml:445`, `confidence: 1.0`) e
 * **não casa nenhuma das 23 regras** do tenant: `*.telefone` exige sufixo
 * `.telefone`, e não há catch-all de `session.*` (não pode haver — derrubaria a
 * tela de aprovação). Cai no `default_unmatched_operator: "plain"`. É a §1.1 do ADR
 * acontecendo num campo concreto.
 *
 * ⚠️ **E o desfecho foi o OPOSTO do previsto, por decisão do dono (2026-08-30).**
 * A V3 o declarou `phone`, presumindo que exibi-lo em claro fosse o defeito. Medido
 * ao decidir: este número não é dado de CADASTRO, é o **objeto do atendimento** — o
 * operador não conclui uma portabilidade sem vê-lo. O campo segue em claro, agora
 * **declarado** como tal (`tipo: "linha_em_servico"`), e não mais por omissão.
 * A §1.1 continua valendo: o defeito nunca foi o valor visível, foi o valor visível
 * porque **ninguém decidiu**. Ver o tipo em `audit.ts` — a finalidade entrou como
 * TIPO, e não como exceção de regra, para que mapa e regra não deem duas respostas.
 */
export const DEFAULT_CONTEXT_MAP: ContextMap = {
  mode:             "audit",
  dynamic_prefixes: ["agent.", "segment.", "core.segment."],
  contexto: {
    // ── core.* — RESERVADO à plataforma (CNS-02). Semeado; o cadastro recusa
    //    root `core` vindo de tenant. Cada folha traz a canônica ANTERIOR no
    //    `legado`: o snapshot durável guarda os nomes velhos para sempre, e é o
    //    alias que mantém aquele histórico mascarado.
    core: {
      contact: {
        close_origin: { tipo: "texto", legado: ["session.contato.close_origin", "session.close_origin"] },
        customer_participant_id: { tipo: "texto", legado: ["session.contato.customer_participant_id", "session.customer_participant_id"] },
        human_agent_participant_id: { tipo: "texto", legado: ["session.contato.human_agent_participant_id", "session.human_agent_participant_id"] },
        last_primary_agent_key: { tipo: "texto", legado: ["session.contato.last_primary_agent_key", "session.last_primary_agent_key"] },
        last_primary_segment_id: { tipo: "texto", legado: ["session.contato.last_primary_segment_id", "session.last_primary_segment_id"] },
        root_session_id: { tipo: "texto", legado: ["session.contato.root_session_id", "session.root_session_id"] },
        spawn_reason: { tipo: "texto", legado: ["session.contato.spawn_reason", "session.spawn_reason"] },
      },
      copilot: {
        last_analysis: { tipo: "texto", legado: ["session.copilot.ultima_analise"] },
        recommended_actions: { tipo: "texto", legado: ["session.copilot.acoes_recomendadas"] },
        risk_flags: { tipo: "texto", legado: ["session.copilot.flags_risco"] },
        suggested_reply: { tipo: "texto", legado: ["session.copilot.sugestao_resposta"] },
      },
      pool: {
        agent_groups: { tipo: "texto", legado: ["session.pool.agent_groups"] },
        channels: { tipo: "texto", legado: ["session.pool.channels"] },
        id: { tipo: "texto", legado: ["session.pool.id"] },
        llm_account_ids: { tipo: "texto", legado: ["session.pool.llm_account_ids"] },
        max_reply_time_ms: { tipo: "texto", legado: ["session.pool.max_reply_time_ms"] },
        mentionable_pools: { tipo: "texto", legado: ["session.pool.mentionable_pools"] },
      },
      process: {
        outcome: { tipo: "texto", legado: ["session.processo.outcome", "session.process_outcome"] },
      },
      queue: {
        eta_ms: { tipo: "texto", legado: ["session.queue.eta_ms"] },
        position: { tipo: "texto", legado: ["session.queue.position"] },
      },
      sentiment: {
        category: { tipo: "texto", label: "Classificada na LEITURA — sem produtor próprio", legado: ["session.sentimento.categoria"] },
        current: { tipo: "texto", legado: ["session.sentimento.current"] },
      },
      survey: {
        agent_key: { tipo: "texto", legado: ["session.survey.agent_key", "session.survey_agent_key", "session.surveyed_agent_key"] },
        grain: { tipo: "texto", legado: ["session.survey.grain", "session.survey_grain"] },
        pool_id: { tipo: "texto", legado: ["session.survey.pool_id", "session.survey_pool_id"] },
        segment_id: { tipo: "texto", legado: ["session.survey.segment_id", "session.survey_segment_id", "session.surveyed_segment_id"] },
        target_id: { tipo: "texto", legado: ["session.survey.target_id", "session.survey_target_id"] },
      },
      workflow: {
        current_round: { tipo: "texto", legado: ["session.workflow.current_round", "session.current_round"] },
        delegate_resume_token: { tipo: "credential", legado: ["session.workflow.delegate_resume_token", "session.delegate_resume_token"] },
        dialog_form_id: { tipo: "texto", legado: ["session.workflow.dialog_form_id", "session.dialog_form_id"] },
        origin_session_id: { tipo: "texto", legado: ["session.workflow.origin_session_id", "session.origin_session_id"] },
        resume_token: { tipo: "credential", legado: ["session.workflow.resume_token", "session.workflow_resume_token"] },
        review_decision: { tipo: "texto", legado: ["session.workflow.review_decision", "session.review_decision"] },
        round_echoed: { tipo: "texto", legado: ["session.workflow.round_echoed", "session.round_echoed"] },
      },
    },
    session: {
      // ── Dados do cliente — hoje no namespace `caller.*` ──────────────────
      cliente: {
        nome:              { tipo: "texto",      legado: ["caller.nome"] },
        cpf:               { tipo: "cpf",        legado: ["caller.cpf", "session.cpf"] },
        telefone:          { tipo: "phone",      legado: ["caller.telefone"] },
        email:             { tipo: "email_addr", legado: ["caller.email"] },
        customer_id:       { tipo: "texto",      legado: ["caller.customer_id", "session.customer_id"], label: "ID interno — não-PII, necessário p/ histórico/360" },
        account_id:        { tipo: "texto",      legado: ["caller.account_id"] },
        motivo_contato:    { tipo: "texto",      legado: ["caller.motivo_contato", "session.motivo_contato"] },
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
        // `cpf`, e não `cpf_titular` (decisão do dono, 2026-08-30): o discriminador
        // mora no segmento de DOMÍNIO, nunca no nome da folha. Medido — o casador de
        // regra NÃO tem glob de meio (`context-masking.ts:80-160` aceita exato,
        // `*.sufixo`, `prefixo.*` e `*`), então um `*cpf*` seria regra INERTE, sem
        // nada ficar vermelho. Com a canônica terminando em `.cpf`, o glob genérico
        // já a cobre: princípio e mecanismo coincidem.
        //
        // ⚠️ A grafia legada NÃO tem produtor — o campo de tela saiu do formulário e
        // foi substituído por `vencimento_cartao` (`skill_limite_processo_v1.yaml:88-91`;
        // o form vivo tem 4 campos e nenhum é ele). O alias fica para que o CONTADOR
        // prove o fóssil extinto em vez de nós afirmarmos: apagá-lo faria a tag, se
        // algum tenant ainda a escrever, cair em "não declarada" sem ninguém saber
        // que ela já fora prevista.
        cpf:               { tipo: "cpf",         legado: ["session.cpf_titular"] },
        // Entrou em 2026-08-30, junto do tipo `card_expiry`. Ver o critério de
        // semeadura no cabeçalho: o campo esperava TIPO, não decisão.
        vencimento:        { tipo: "card_expiry", legado: ["session.vencimento_cartao"] },
        limite_solicitado: { tipo: "financial",   legado: ["session.limite_solicitado"] },
        limite_aprovado:   { tipo: "financial",   legado: ["session.limite_aprovado"] },
      },
      // ── Já CANÔNICOS: escritos em `escopo.dominio.campo` pelo routing-engine ──
      // Nenhum `legado`, e é isso que dá ao contador do D3 o seu par: sem tag
      // canônica viva, "ninguém migrou" e "ninguém usa" seriam indistinguíveis.
      // ── Copiloto — as 4 saidas do `copilot_emitter` + o interruptor ──────
      //
      // ⚠️ Este dominio e' NOVO no mapa, e a nota que precedeu esta fatia dizia
      // para LISTAR ao dono em vez de cria-lo. A medicao mudou o veredicto, e o
      // motivo importa: as cinco tags JA sao escritas em `escopo.dominio.campo`
      // pela propria plataforma (`copilot_emitter.py`, `server.ts:2031-2034`,
      // `skill.ts:1390`). Declara-las nao escolhe nome nenhum — descreve o que
      // existe, com zero alias. O que a nota protegia era INVENTAR taxonomia; nao
      // declarar aqui deixaria tag canonica da propria plataforma em `unknown`,
      // que e' o defeito, nao a prudencia. A decisao aberta #3 (lista de dominios
      // por PAPEL, D9.8) renormaliza este dominio junto com os outros 14 — todos
      // os 15 mudam de nome quando ela cair, e este nao acrescenta divida.
      //
      // Tres deles sao PROSA de LLM sobre a conversa; `texto` e' aposta, nao
      // descricao (ver o censo, § "Quantos ficam sem tipo obvio"). Ficam `texto`
      // porque a alternativa quebra a tela: o painel do copiloto no Console le os
      // quatro pela porta de masking.
      copilot: {
        mode:                { tipo: "texto", label: "Interruptor — `mention.set_context`" },
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
      // ⚠️ As grafias `session.*` PLANAS abaixo não são cosmética: são o que o código
      // realmente escreve. A V3 declarou as canônicas e deixou 13 folhas SEM `legado`,
      // então a grafia viva caía em `unknown` — medido em 2026-08-30, com tráfego real,
      // e `session.dialog_form_id`/`session.decisions` entre elas. Inverter a V4 assim
      // derrubaria a tela de aprovação em silêncio, que é exatamente o que o seed do
      // config-api avisa por escrito. Os aliases vêm do CENSO DE PRODUTORES
      // (`ctx_writes` do channel-gateway + escritas do bridge + `tag:` dos YAML), não
      // de semelhança de nome.
      workflow: {
        max_rounds:            { tipo: "texto", legado: ["session.max_rounds"] },
        decisions:             { tipo: "texto", legado: ["session.decisions"] },
        briefing_session_id:   { tipo: "texto", legado: ["session.briefing_session_id"] },
        // ── Pacote de aprovacao — MESMO `delegate.context` que ja deposita
        // `dialog_form_id` e `decisions` acima. Nao e' dominio novo: e' o resto do
        // payload que a V3 declarou pela metade.
        // (`skill_limite_processo_v1.yaml:88-104`, `skill_gate_promocao_v1.yaml:52-58`.)
        title:                 { tipo: "texto", legado: ["session.title"] },
        // ⚠️ DOIS aliases porque sao DUAS SESSOES para UM fato — a sessao do
        // processo escreve `approval.summary` (`invoke context_set`) e a filha
        // recebe o MESMO valor como `session.summary`, piped pelo
        // `summary: "@ctx.approval.summary"` do delegate. Mesma forma de
        // `caller.cpf` x `session.cpf`.
        //
        // ⚠️ E o tipo e' `texto` por MEDICAO, nao por preguica: `session.summary`
        // e' lido em `DialogFormRenderer.tsx:232` ATRAVES da porta de masking, entao
        // um tipo restritivo apaga a tela de aprovacao — o "troca vazamento de PII
        // por quebra muda de UI" que o pre-requisito da V4 nomeia. O YAML ja carrega
        // a contramedida por escrito: o summary carrega so texto publico, e cartao,
        // CPF e valor viajam como tags SEPARADAS justamente para terem politica
        // propria (`skill_limite_processo_v1.yaml:39-42`).
        summary:               { tipo: "texto", legado: ["session.summary", "approval.summary"] },
        status:                { tipo: "texto", legado: ["session.status"] },
        approval_threshold:    { tipo: "texto", legado: ["session.approval_threshold"] },
        // Revisao de avaliacao (evaluation-api `router.py:2336-2338`, `:2455-2457`).
        // `round_echoed` e' o par de `current_round` acima; `review_decision`, o de
        // `decisions` — mesma familia, mesmo dominio.
      },
      contato: {
        contact_channel:            { tipo: "texto", legado: ["session.contact_channel"] },
        contact_identifier:         { tipo: "texto", legado: ["session.contact_identifier"] },
        contact_outcome:            { tipo: "texto", legado: ["session.contact_outcome"] },
        customer_present:           { tipo: "texto", legado: ["session.customer_present"] },
        confirmation_channel:       { tipo: "texto", legado: ["session.confirmation_channel"] },
        // ── Proveniencia do ACESSO. Escritas juntas pelo gateway em todo nascimento
        // de sessao (`webhook.py:609-625`, `:1665-1687`, `:2183-2195`).
        // `spawn_reason` e' o discriminador ternario da D13 (NULL=inbound ·
        // `collect`=outbound · `trigger`/`delegate`=interno).
        resume_origin:              { tipo: "texto", legado: ["session.resume_origin"] },
        // ── Fatos que o bridge carimba PRE-HOOK, para o hook saber quem atendeu
        // (`main.py:1469`, `:1491`). Sao a fonte dos aliases `surveyed_*` logo abaixo.
        // Pergunta consolidada que a IA GERA e o menu seguinte interpola
        // (`agente_contexto_ia_v1.yaml:170-175`). Prosa de LLM — e `texto` aqui nao e'
        // omissao: o valor e' exibido AO CLIENTE, entao mascara-lo quebra a coleta.
        pergunta_coleta:            { tipo: "texto", legado: ["session.pergunta_coleta"] },
      },
      // ⚠️ As 9 canonicas abaixo ja existiam desde a V3 e TODAS estavam sem
      // `legado` — a grafia viva e' PLANA (`session.survey_form_id`), composta pelo
      // gateway e pelos `context_json`, e caia em `unknown`. E' o mesmo defeito que o
      // cabecalho do `workflow` documenta: canonica declarada, grafia real orfa.
      //
      // ⚠️ E as duas folhas `surveyed_*` SUMIRAM daqui — nao foram apagadas, foram
      // FUNDIDAS. Medido no censo: o bridge escreve `session.surveyed_segment_id` /
      // `surveyed_agent_key` no `on_human_end` da sessao de ORIGEM (`main.py:2239`) e
      // o gateway escreve `session.survey_segment_id` / `survey_agent_key` no
      // `collect_engage` da sessao da PESQUISA (`webhook.py:2231`). UM fato — *qual
      // segmento/agente esta sendo pesquisado* —, duas sessoes, duas grafias. Manter
      // duas canonicas para um fato e' a D9.8 acontecendo: em seis meses "o segmento
      // pesquisado" teria duas casas defensaveis.
      survey: {
        form_id:             { tipo: "texto", legado: ["session.survey_form_id"] },
        origin:              { tipo: "texto", legado: ["session.survey_origin"] },
        origin_pool:         { tipo: "texto", legado: ["session.survey_origin_pool"] },
        customer_key:        { tipo: "texto", legado: ["session.survey_customer_key"] },
      },
      portabilidade: {
        // `linha_em_servico`, não `phone` — decisão do dono, 2026-08-30: é a linha
        // SENDO PORTADA, objeto do atendimento e não dado de cadastro. O telefone de
        // CADASTRO continua protegido, em `session.cliente.telefone`. Ver o achado de
        // exposição no cabeçalho e o tipo em `audit.ts`.
        numero_atual:      { tipo: "linha_em_servico", legado: ["session.numero_atual"] },
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
      // ── Processo, no escopo da SESSAO. Nao e' dominio inventado: e' o
      // `journey.processo` que ja existe abaixo, um escopo acima. O censo mediu que
      // `session.parecer`/`session.resultado` sao os gemeos de
      // `journey.parecer`/`journey.resultado` — mesmo tipo, escopo diferente —, e o
      // escopo e' o PRIMEIRO segmento por decisao da D2, logo nao podem ser alias
      // um do outro. Chegam pelo `delegate.context` de `skill_limite_entrada_v1.yaml:410-412`.
      //
      // `outcome` e' o grao de PROCESSO, e a distincao dele para
      // `contato.contact_outcome` esta escrita pelo proprio consumidor:
      // `skill_survey_trigger_v1.yaml:65-68` testa os dois porque "so uma delas
      // existe em cada disparo" — `on_process_end` x `on_contact_end`. Carimbado
      // pelo bridge pre-hook (`main.py:5284`).
      processo: {
        parecer:   { tipo: "texto", legado: ["session.parecer"] },
        resultado: { tipo: "texto", legado: ["session.resultado"] },
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
// Carimbo do atributo (D9.6) — a metade PURA do choke point de escrita
// ─────────────────────────────────────────────

/**
 * O carimbo que uma entrada do ContextStore carrega depois de passar pelo funil.
 *
 * `atributo` AUSENTE numa entrada significa uma coisa só: **ela não passou pelo funil**.
 * É por isso que `dynamic` e `unknown` também carimbam — sem eles, a entrada de uma tag
 * não cadastrada ficaria byte a byte igual à de um `HSET` direto, e o furo que a D9.6
 * chama de silencioso continuaria silencioso, agora com a aparência de cobertura.
 */
export interface ContextEntryStamp {
  /** Tipo do catálogo. Ausente em `dynamic`/`unknown` — não há folha que o declare. */
  tipo?:     string
  origem:    ContextTagOrigin
  /** Só em `alias`: o nome canônico. O valor fica gravado sob a grafia legada. */
  canonica?: string
  /**
   * Só quando o mapa usado foi o EMBUTIDO (config-api inalcançável). O carimbo continua
   * acontecendo — recusar deixaria a escrita refém da config —, mas deixa de afirmar o
   * que o TENANT declarou, e num export LGPD essa distinção precisa sobreviver.
   * Ausente, nunca `false`: a entrada vai para toda folha de todo hash de ctx.
   */
  fallback?: true
}

/**
 * Carimba o `atributo` numa entrada do ContextStore a partir do cadastro (D9.6).
 *
 * ⚠️ **Esta função é ESPELHADA em Python** (ALW-02 passo 2) e um gate compara as duas
 * saídas. Ela é pura e sem I/O exatamente por isso: função pura é a única espécie que se
 * cross-checa barato entre duas linguagens. Qualquer mudança aqui move o gate junto.
 *
 * ⚠️ **Departura declarada da regra de língua** (`CLAUDE.md` § Language Rule): os nomes
 * `atributo`/`tipo`/`origem`/`canonica` são português porque o MAPA já é
 * (`contexto.escopo.dominio.campo` com `tipo`/`legado`), e o valor de `tipo` vem verbatim
 * de lá. Nomear o destino diferente da fonte convida a pergunta *"são o mesmo fato?"*,
 * que é o custo que esta linha compra para evitar. Dívida do vocabulário do mapa, não nova.
 *
 * Nunca muta a entrada recebida — o escritor reusa o objeto num `mapping=` de N tags, e
 * mutar faria a segunda herdar o carimbo da primeira.
 */
export function stampContextEntry(
  entry:         Record<string, unknown>,
  tag:           string,
  index:         ContextTagIndex,
  mapIsFallback: boolean,
): Record<string, unknown> {
  const r = resolveContextTag(tag, index)

  const atributo: ContextEntryStamp = { origem: r.origin }
  if (r.tipo !== undefined)      atributo.tipo     = r.tipo
  if (r.origin === "alias")      atributo.canonica = r.canonical
  if (mapIsFallback)             atributo.fallback = true

  return { ...entry, atributo }
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
  /**
   * Root cujo NOME anuncia um store para o qual as tags dele não roteiam — o mapa
   * prometendo uma retenção que ninguém aplica. Substituiu `unknown_scopes`, que
   * gateava o root contra um enum escrito à mão e por isso aprovava justamente o
   * caso perigoso (`customer`, cujo prefixo não roteia para lugar nenhum).
   */
  mismatched_retention:    Array<{ root: string; anuncia: ContextStoreKind; roteia_para: ContextStoreKind }>
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
  const mismatched_retention: Array<{ root: string; anuncia: ContextStoreKind; roteia_para: ContextStoreKind }> = []
  const claims = new Map<string, string[]>()   // legado → canônicas que o reivindicam

  for (const [escopo, dominios] of Object.entries(map.contexto)) {
    // O root é LIVRE (CNS-02): `core` é da plataforma, o resto é do tenant, e tudo o
    // que não tem rota própria vive na sessão — que é honesto e é o default real.
    // O que NÃO pode é o root anunciar um store e as tags dele irem para outro: é a
    // retenção prometida sem mecanismo. Só se confere quando o nome do root É um
    // store; um root de negócio (`card`, `products`) não anuncia nada.
    const anuncia = ContextStoreKindSchema.safeParse(escopo)
    if (anuncia.success && anuncia.data !== "session") {
      const campo0 = Object.keys(dominios)[0]
      const folha0 = campo0 !== undefined ? Object.keys(dominios[campo0] ?? {})[0] : undefined
      const amostra = folha0 !== undefined ? `${escopo}.${campo0}.${folha0}` : `${escopo}.`
      const real = resolveContextStore(amostra)
      if (real !== anuncia.data) {
        mismatched_retention.push({ root: escopo, anuncia: anuncia.data, roteia_para: real })
      }
    }
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
    mismatched_retention,
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
