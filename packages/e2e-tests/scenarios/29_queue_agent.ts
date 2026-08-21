/**
 * 29_queue_agent.ts
 * Scenario 29: FILA ATENDIDA — o agente de fila OUVE o cliente
 *
 * ── Por que este cenário existe ──────────────────────────────────────────────
 * Em 2026-08-24 descobriu-se que o agente de fila era **surdo à mensagem do
 * cliente**: quem esperava na fila podia escrever à vontade e nada chegava ao
 * agente. O defeito viveu meses e nenhum cenário o pegou, porque **nenhum
 * cenário exercitava um contato entrando em fila com o agente de fila rodando**.
 * O cenário 07 chama `queue_context_get` direto por MCP, sem flow algum — e a
 * asserção dele sobre a fila é um `pass()` incondicional.
 *
 * ── O que tornava o defeito invisível ────────────────────────────────────────
 * O agente de fila é o **único caller em produção ativado com `instance_id=""`**
 * (`orchestrator-bridge/main.py:5952`). Esse valor decide DOIS nomes que
 * precisam casar:
 *
 *   · o campo do hash `menu:waiting:{sid}` — escrito pelo engine
 *     (`menu.ts:229`, hoje `ctx.instanceId || "_default_"`)
 *   · a lista do BLPOP — `redis-keys.ts:27-30`, por truthiness:
 *     `instanceId ? menu:result:{sid}:{iid} : menu:result:{sid}`
 *
 * Com `??` no lugar de `||`, a string vazia SOBREVIVIA no campo do hash mas era
 * FALSY na chave: o engine escutava `menu:result:{sid}` e o bridge — que testa
 * `campo !== "_default_"` — entregava em `menu:result:{sid}:` (dois-pontos
 * final), lista que ninguém consome. *O campo é `_default_` se e somente se a
 * chave não tem sufixo* — é essa relação que este cenário verifica, e não cada
 * literal em separado.
 *
 * Metade da funcionalidade continuava viva e escondia o resto: o sentinela
 * `__agent_available__` é publicado pelo routing numa chave session-scoped
 * HARDCODED (`kafka_listener.py:728`), então a transferência funcionava. Só a
 * fala do cliente sumia.
 *
 * ── Camadas de asserção (cada falha aponta para UMA causa) ───────────────────
 *   P — precondições MEDIDAS (sem humano pronto · pool com `queue_config`)
 *   A — o contato enfileira e o bridge ativa o agente de fila
 *   B — TRANSPORTE: campo `_default_` + testemunha negativa da lista-fantasma
 *   C — o agente OUVIU (`ultima_mensagem` no pipeline_state) ← pega a surdez
 *   D — o agente RESPONDEU (depende de LLM real; asserção própria, para que a
 *       falha diga "LLM", nunca "surdez")
 *
 * Assertions: 8 (P1 P2 · A1 A2 · B1 B2 · C1 · D1)
 *
 * ⚠️ O cenário PODE terminar com menos de 8: P1, P2, A1 e A2 são portões e
 * retornam cedo quando falham. Isso é deliberado — sem contato enfileirado não
 * existe agente de fila, e as asserções seguintes não teriam o que julgar; um
 * verde delas seria cobertura falsa. **Contagem menor que 8 significa portão
 * fechado, nunca sucesso parcial.**
 */

import { randomUUID } from "crypto"
import fetch from "node-fetch"
import type { ScenarioContext, ScenarioResult, Assertion } from "./types"
import { WsTestClient }          from "../lib/ws-client"
import { mintFreshWebchatToken } from "../lib/jwt-helper"
import { seedSessionMeta, getPipelineState } from "../lib/redis-client"
import { pass, fail }            from "../lib/report"

// ── Config ────────────────────────────────────────────────────────────────────

/** Pool humano de destino. É o único do demo com `queue_config` (ver P2). */
const TARGET_POOL = "retencao_humano"

/** Ativação do bridge: consumir `conversations.queued` + resolver o flow. */
const ACTIVATION_TIMEOUT_MS = 25_000
/** Entrega da fala: gateway → Kafka → bridge → LPUSH → BLPOP → engine. */
const HEARD_TIMEOUT_MS      = 20_000
/** Resposta do agente: inclui uma chamada REAL de LLM no step `reason`. */
const REPLY_TIMEOUT_MS      = 45_000

// ── Helpers ───────────────────────────────────────────────────────────────────

function asMsg(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>
  throw new Error(`Esperado objeto, veio: ${JSON.stringify(raw)}`)
}

/** Poll até `check` devolver algo não-nulo, ou estourar o prazo. */
async function poll<T>(
  check: () => Promise<T | null>,
  timeoutMs: number,
  intervalMs = 500,
): Promise<T | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await check()
    if (value !== null && value !== undefined) return value
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return null
}

/**
 * Procura o valor de `output_as` do step `menu` no pipeline_state.
 *
 * O documento tem forma própria (`current_step_id`, `status`, `results`, …) e o
 * cenário não deve DEPENDER de adivinhar onde o engine grava — por isso procura
 * nos dois lugares plausíveis e, quando não acha, devolve as CHAVES vistas para
 * o payload da falha. Um "não achei" que não diz onde procurou é indistinguível
 * de "o agente não ouviu", que é justamente a distinção deste cenário.
 */
function findMenuOutput(
  state: unknown,
  field: string,
): { value: unknown; where: string } | null {
  if (!state || typeof state !== "object") return null
  const doc = state as Record<string, unknown>

  if (field in doc) return { value: doc[field], where: "pipeline_state" }

  const results = doc["results"]
  if (results && typeof results === "object" && field in (results as Record<string, unknown>)) {
    return { value: (results as Record<string, unknown>)[field], where: "pipeline_state.results" }
  }
  return null
}

function stateKeys(state: unknown): string[] {
  if (!state || typeof state !== "object") return []
  const doc  = state as Record<string, unknown>
  const top  = Object.keys(doc)
  const res  = doc["results"] && typeof doc["results"] === "object"
    ? Object.keys(doc["results"] as Record<string, unknown>).map((k) => `results.${k}`)
    : []
  return [...top, ...res]
}

// ── Scenario ──────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt = Date.now()
  const assertions: Assertion[] = []

  const finish = (error?: string): ScenarioResult => ({
    scenario_id: "29",
    name:        "Queue Agent — fila atendida ouve o cliente",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
    ...(error ? { error } : {}),
  })

  // ══ P — PRECONDIÇÕES, MEDIDAS ══════════════════════════════════════════════
  // Não são "setup": são as duas condições sem as quais um verde abaixo não
  // significaria nada. Medidas, nomeadas, e reprovando alto quando falsas.

  // P1 — ninguém pronto no pool humano ⇒ o contato ENFILEIRA em vez de ser
  // roteado. O `flushTestData` do runner apaga `{tenant}:*` antes de cada
  // cenário, e o ready_set (`{t}:pool:{p}:instances`) morre junto — instância
  // humana não é gerida pelo Bootstrap e heartbeat não recria instância
  // (criação é do login). Na prática isto é verdade mesmo com o Console aberto;
  // o que ainda pode quebrá-la é um re-login DENTRO da janela do cenário.
  // Por isso: medir, nunca assumir.
  const readySetKey = `${ctx.tenantId}:pool:${TARGET_POOL}:instances`
  const readyCount  = await ctx.redis.scard(readySetKey)
  assertions.push(
    readyCount === 0
      ? pass("P1: nenhum agente pronto em " + TARGET_POOL + " ⇒ o contato vai enfileirar", {
          ready_set: readySetKey,
        })
      : fail(
          "P1: há agente PRONTO em " + TARGET_POOL + " — o contato seria roteado, não enfileirado",
          {
            ready_set: readySetKey,
            membros:   await ctx.redis.smembers(readySetKey),
            causa:
              "alguém fez agent_login depois do flush do runner (Console aberto que " +
              "reconectou). Deslogue do Console e repita — um verde aqui, com humano " +
              "pronto, provaria o caminho ERRADO.",
          },
        ),
  )
  if (readyCount !== 0) return finish("P1 falhou — precondição de fila não satisfeita")

  // P2 — o pool de destino declara `queue_config`. Sem ele a fila é MUDA
  // (`main.py:808-837`): o contato espera em silêncio e o agente de fila NUNCA
  // é ativado. Seria regressão de CONFIG, não de código, e tem de dizer isso.
  let queueCfg: Record<string, unknown> | null = null
  try {
    const res = await fetch(`${ctx.agentRegistryUrl}/v1/pools/${TARGET_POOL}`, {
      headers: { "x-tenant-id": ctx.tenantId },
    })
    if (res.ok) {
      const pool = (await res.json()) as Record<string, unknown>
      queueCfg = (pool["queue_config"] as Record<string, unknown> | null) ?? null
    }
  } catch { /* tratado na asserção */ }

  assertions.push(
    queueCfg && typeof queueCfg["pool_id"] === "string"
      ? pass("P2: " + TARGET_POOL + " declara queue_config (fila ATENDIDA)", {
          queue_pool: queueCfg["pool_id"],
        })
      : fail("P2: " + TARGET_POOL + " sem queue_config — a fila seria MUDA e o agente nunca ativa", {
          queue_config: queueCfg,
          nota: "config, não código: o agente de fila só existe quando o pool de destino o declara",
        }),
  )
  if (!queueCfg) return finish("P2 falhou — pool sem queue_config")
  const queuePoolId = String(queueCfg["pool_id"])

  // ══ Contato ════════════════════════════════════════════════════════════════

  // Segredo EFETIVO do webchat, resolvido como o channel-gateway resolve
  // (`webchat.py:466`): Redis `{tenant}:config:webchat:jwt_secret` primeiro,
  // env do runner como fallback. Ler em vez de assumir evita depender de duas
  // linhas de compose mantidas iguais à mão — e, sobretudo, o modo de falha de
  // errar aqui é `invalid_token`, que parece defeito do handshake e não é.
  // Na prática o `flushTestData` do runner apaga a chave do Redis antes de cada
  // cenário, então o ramo normal é o fallback; o outro ramo existe para o caso
  // de alguém rodar sem flush.
  const perTenantSecret = await ctx.redis.get(`${ctx.tenantId}:config:webchat:jwt_secret`)
  const secretSource    = perTenantSecret ? "redis:config:webchat:jwt_secret" : "env:WEBCHAT_JWT_SECRET"
  const { token, contactId, sessionId } = mintFreshWebchatToken({
    tenantId:  ctx.tenantId,
    jwtSecret: perTenantSecret || ctx.webchatJwtSecret,
  })
  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, contactId)

  const client = new WsTestClient(`${ctx.channelGatewayWsUrl}/ws/chat/${TARGET_POOL}`)

  // Marcador único: garante que a igualdade de texto em C1 não possa casar por
  // acaso com nenhuma outra mensagem da sessão.
  const marker      = randomUUID().slice(0, 8)
  const customerSay = `Já é a terceira vez que entro em contato e ninguém resolve [${marker}]`

  try {
    await client.connect()

    // ── A1: handshake ────────────────────────────────────────────────────────
    await client.receive(5000)                                  // conn.hello
    client.send({ type: "conn.authenticate", token, cursor: null })
    const authed = asMsg(await client.receive(8000))
    assertions.push(
      authed["type"] === "conn.authenticated" && authed["session_id"] === sessionId
        ? pass("A1: webchat autenticado", { session_id: sessionId, segredo: secretSource })
        : fail("A1: handshake do webchat falhou", {
            authed,
            segredo_usado: secretSource,
            nota:
              "`invalid_token` aqui NÃO é defeito do handshake: é o segredo do runner " +
              "divergindo do que o channel-gateway usa. Ele resolve em Redis " +
              "`{tenant}:config:webchat:jwt_secret` → `PLUGHUB_JWT_SECRET` " +
              "(docker-compose.demo.yml §1229). Conferir WEBCHAT_JWT_SECRET do e2e-runner.",
          }),
    )
    if (authed["type"] !== "conn.authenticated") return finish("A1 falhou — sem contato, nada a julgar")

    // ── A2: o bridge ativou o agente de fila ─────────────────────────────────
    // `queue:agent_active:{sid}` é gravado por `process_queued` DEPOIS de
    // resolver o flow (`main.py:5898`). Existir prova três coisas de uma vez:
    // o routing publicou `conversations.queued`, o bridge consumiu, e o flow
    // do pool de fila resolveu.
    const activeKey = `queue:agent_active:${sessionId}`
    const activated = await poll(
      async () => ((await ctx.redis.exists(activeKey)) === 1 ? true : null),
      ACTIVATION_TIMEOUT_MS,
    )
    assertions.push(
      activated
        ? pass("A2: bridge ativou o agente de fila (queue:agent_active presente)", { key: activeKey })
        : fail("A2: agente de fila NÃO foi ativado em " + ACTIVATION_TIMEOUT_MS + "ms", {
            key: activeKey,
            causas_ordenadas: [
              "(a) o contato foi ROTEADO, não enfileirado — mas P1 mediu ready_set vazio",
              "(b) o routing não publicou conversations.queued",
              "(c) o bridge não resolveu o flow do pool de fila " + queuePoolId +
                " (sem esse flow ele aborta ANTES de gravar o marcador)",
              "(d) o orchestrator-bridge não está de pé — ele não tem health HTTP, " +
                "então o compose só garante `service_started`",
            ],
          }),
    )
    if (!activated) return finish("A2 falhou — sem agente de fila não há o que ouvir")

    // ── B1: TRANSPORTE — o campo do hash é `_default_`, nunca vazio ──────────
    // Esta é a asserção que teria pego a surdez, e ela é determinística: não
    // depende de LLM, de timing de resposta nem de conteúdo.
    const waitingKey = `menu:waiting:${sessionId}`
    const waitingHash = await poll(
      async () => {
        const h = await ctx.redis.hgetall(waitingKey)
        return h && Object.keys(h).length > 0 ? h : null
      },
      ACTIVATION_TIMEOUT_MS,
    )
    const waitingFields = waitingHash ? Object.keys(waitingHash) : []
    assertions.push(
      waitingFields.includes("_default_")
        ? pass("B1: menu:waiting tem o campo `_default_` (instance_id vazio normalizado)", {
            key: waitingKey, campos: waitingFields,
          })
        : fail("B1: campo `_default_` AUSENTE em menu:waiting — a surdez do agente de fila voltou", {
            key:    waitingKey,
            campos: waitingFields,
            nota:
              'campo "" (vazio) significa `??` no lugar de `||` em menu.ts/resolve.ts: ' +
              "a string vazia sobrevive no nome do campo mas é falsy na chave do BLPOP, " +
              "e o bridge passa a escrever numa lista que ninguém consome",
          }),
    )

    // ── C1: o agente OUVIU ───────────────────────────────────────────────────
    client.send({ type: "msg.text", id: randomUUID(), text: customerSay })

    const heard = await poll(
      async () => {
        const state = await getPipelineState(ctx.redis, ctx.tenantId, sessionId)
        const found = findMenuOutput(state, "ultima_mensagem")
        return found && found.value === customerSay ? found : null
      },
      HEARD_TIMEOUT_MS,
    )

    const lastState = await getPipelineState(ctx.redis, ctx.tenantId, sessionId)
    assertions.push(
      heard
        ? pass("C1: o agente de fila OUVIU a fala do cliente", {
            onde: heard.where, texto: customerSay,
          })
        : fail("C1: a fala do cliente NÃO chegou ao agente em " + HEARD_TIMEOUT_MS + "ms", {
            esperado:            customerSay,
            visto_em_pipeline:   findMenuOutput(lastState, "ultima_mensagem")?.value ?? "<ausente>",
            chaves_do_state:     stateKeys(lastState),
            nota:
              "é EXATAMENTE o defeito de 2026-08-24. Conferir a cadeia: " +
              "channel-gateway → conversations.inbound → bridge process_inbound → " +
              "HGETALL menu:waiting → LPUSH menu:result:{sid} → BLPOP do engine",
          }),
    )

    // ── B2: TESTEMUNHA NEGATIVA — a lista-fantasma não pode existir ──────────
    // `menu:result:{sid}:` (dois-pontos FINAL) é a lista que o bug criava: o
    // bridge derivava a chave de um campo vazio que não é `_default_`. Ela só
    // pode surgir DEPOIS de uma fala do cliente — por isso a checagem vem aqui,
    // e não junto de B1.
    const ghostKey   = `menu:result:${sessionId}:`
    const ghostExists = await ctx.redis.exists(ghostKey)
    assertions.push(
      ghostExists === 0
        ? pass("B2: lista-fantasma ausente (nada foi escrito em `menu:result:{sid}:`)", { key: ghostKey })
        : fail("B2: a fala foi entregue na lista-fantasma — ninguém a consome", {
            key:      ghostKey,
            conteudo: await ctx.redis.lrange(ghostKey, 0, 4),
          }),
    )

    // ── D1: o agente RESPONDEU (metade que depende de LLM real) ─────────────
    // Asserção separada de propósito: o step `responder_cliente` chama o
    // ai-gateway, e o `on_failure` dele volta ao menu SEM falar com o cliente
    // (`agente_fila_v1.yaml:111`) — falha de LLM na fila é, para o cliente,
    // indistinguível de "o agente ignorou". Aqui as duas coisas têm nomes
    // diferentes: C1 verde + D1 vermelho = o agente ouviu e o LLM falhou.
    // ⚠️ A v1 desta asserção lia o WEBSOCKET — "o primeiro texto que não contém o
    // marcador" — e passava em ~0 ms, casando com a SAUDAÇÃO (`boas_vindas`), que é
    // um `notify` anterior à fala do cliente e naturalmente não tem o marcador.
    // Passaria com o `responder_cliente` completamente morto. O tell foi a DURAÇÃO:
    // 1059 ms para um cenário que inclui uma chamada real de LLM. *Verde rápido
    // demais é hipótese, não resultado.*
    //
    // Hoje julga o ARTEFATO do step: `resposta_ia` é o `output_as` do
    // `responder_cliente` (`agente_fila_v1.yaml`) e só existe se o `reason` teve
    // sucesso — o `on_failure` desvia para `aguardar_mensagem` sem gravá-lo. Não
    // depende de ordem de chegada no WS nem do texto configurado da saudação.
    const answered = await poll(
      async () => {
        const state = await getPipelineState(ctx.redis, ctx.tenantId, sessionId)
        const found = findMenuOutput(state, "resposta_ia")
        const value = found?.value as Record<string, unknown> | undefined
        const text  = value && typeof value["resposta"] === "string" ? (value["resposta"] as string) : ""
        return text.length > 0 ? { where: found!.where, text } : null
      },
      REPLY_TIMEOUT_MS,
    )

    const finalState = await getPipelineState(ctx.redis, ctx.tenantId, sessionId)
    assertions.push(
      answered
        ? pass("D1: o `reason` produziu resposta (resposta_ia no pipeline_state)", {
            onde: answered.where, resposta: answered.text.slice(0, 120),
          })
        : fail("D1: o step `responder_cliente` não produziu resposta em " + REPLY_TIMEOUT_MS + "ms", {
            chaves_do_state: stateKeys(finalState),
            nota:
              "com C1 VERDE isto NÃO é surdez: o agente ouviu. Suspeitos, nesta ordem — " +
              "(a) credencial do LLM recusada; o `reason` cai no `on_failure`, que volta a " +
              "`aguardar_mensagem` SEM falar com o cliente (agente_fila_v1.yaml:111), então " +
              "falha de LLM na fila é indistinguível de 'o agente ignorou' pelo lado do cliente; " +
              "(b) o ai-gateway não respondeu dentro do prazo.",
          }),
    )
  } catch (err) {
    assertions.push(fail("Cenário 29 — erro inesperado", String(err)))
  } finally {
    client.disconnect()
  }

  return finish()
}
