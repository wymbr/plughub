// _arrival_evidence_exercise.cjs — PID-09 (2026-09-14). Roda DENTRO do container do mcp-server:
//   docker exec -i -e T_ADM=… -e WA_SECRET=… <mcp> sh -c 'cd /app/packages/mcp-server-plughub && node - <tenant> [--sem-whatsapp]' < este arquivo
//
// Pelo TRANSPORTE MCP de verdade, com token de sessão emitido pelo emissor interno, contra o
// gateway e o Redis reais. Clientes importados como AUTORITATIVOS (sistema __probe_pid09__);
// pendências escritas no índice do gateway como o delegate as escreve.
//
//   A  cliente que provou OTP na sessão       — o red-first do vetor cruzado
//   B  cliente com posse DURÁVEL e pendência  — a vítima
//   W  cliente que chega pelo WhatsApp        — telefone autoritativo, nunca fez OTP
//   U  número sem cadastro
//
// CASOS (os `r_*` rodam também na imagem antiga; os `w_*`/`u_*`/`nao_assinado_*` só com a nova)
//   r_controle_b             S_B provou B e pede a pendência de B            -> leva o token (controle)
//   r_otp_outro_cliente      S_A provou A e pede a pendência de B (possessed)  -> NÃO leva o token
//   r_resume_outro_recusa    S_A retoma o token de B                          -> resume_requires_unproven
//   w_controle_sem_chegada   sessão sem chegada pede a pendência de W         -> verification_required
//   w_evidencia_verified     webhook assinado do telefone de W                -> whatsapp=verified, customer=W
//   w_libera_sem_otp         a sessão da chegada pede a pendência de W        -> leva o token, sem OTP
//   w_resume_passa           a sessão da chegada retoma o token de W          -> passa o atestado (chega ao gateway)
//   w_outro_cliente_retido   a sessão da chegada pede a pendência de B        -> NÃO leva o token
//   w_resume_outro_recusa    a sessão da chegada retoma o token de B          -> resume_requires_unproven
//   u_failed                 webhook assinado de número sem cadastro          -> whatsapp=failed, sem cliente
//   nao_assinado_400         webhook sem assinatura                           -> 400 e nenhuma sessão
const crypto = require("crypto")
const Redis = require("ioredis")
const { Client } = require("@modelcontextprotocol/sdk/client/index.js")
const { SSEClientTransport } = require("@modelcontextprotocol/sdk/client/sse.js")

const TENANT = process.argv[2] || "tenant_demo"
const SEM_WHATSAPP = process.argv.includes("--sem-whatsapp")
const BASE = "http://localhost:3100"
const GW = process.env.CHANNEL_GATEWAY_URL || "http://channel-gateway:8010"
const SYSTEM = "__probe_pid09__"
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function main() {
  const out = { sem_whatsapp: SEM_WHATSAPP, casos: {}, detalhe: {}, limpeza: {} }
  const c = out.casos
  const redis = new Redis(process.env.REDIS_URL || "redis://redis:6379")
  const suf = String(Date.now()).slice(-7)
  const tel = n => `+55119${n}${suf}`                     // +55 11 9 n ddddddd
  const fones = { A: tel(1), B: tel(2), W: tel(3), U: tel(4) }
  out.limpeza.fones = fones
  const sess = { A: `probe-pid09-a-${suf}`, B: `probe-pid09-b-${suf}`, X: `probe-pid09-x-${suf}` }
  const tokens = { B: `probe-pid09-tkb-${suf}`, W: `probe-pid09-tkw-${suf}` }
  const chaves = []
  out.limpeza.redis = chaves

  const client = new Client({ name: "probe-pid09", version: "0" })
  await client.connect(new SSEClientTransport(new URL(BASE + "/sse")))
  const chama = async (name, args) => {
    const r = await client.callTool({ name, arguments: args })
    let body = {}
    try { body = JSON.parse(r.content?.[0]?.text ?? "{}") } catch { body = { raw: r.content?.[0]?.text } }
    return { isError: r.isError === true, body }
  }
  const emite = async (sid) => {
    const resp = await fetch(BASE + "/internal/session-token", { method: "POST",
      headers: { "content-type": "application/json", "x-service-token": process.env.MCP_INTERNAL_SERVICE_TOKEN || "" },
      body: JSON.stringify({ tenant_id: TENANT, session_id: sid, instance_id: "probe", skill_id: "probe" }) })
    return resp.status === 200 ? (await resp.json()).session_token : ""
  }
  const pendencia = async (tok, fone) => chama("pending_workflow_get", { anchors: [{ kind: "phone", value: fone }], session_token: tok })
  const levou = (r, token) => !r.isError && JSON.stringify(r.body).includes(token)
  const journey = async (sid) => {
    const h = await redis.hgetall(`${TENANT}:ctx:journey:${sid}`)
    chaves.push(`${TENANT}:ctx:journey:${sid}`)
    const v = k => { try { return JSON.parse(h[k]).value } catch { return undefined } }
    return { status: v("core.journey.identity.whatsapp.status"), customer: v("core.journey.identity.whatsapp.customer_id") }
  }

  try {
    // ── cadastro autoritativo ────────────────────────────────────────────────
    const imp = await fetch(`${GW}/v1/channels/webhook/identity/import`, { method: "POST",
      headers: { "content-type": "application/json", authorization: "Bearer " + (process.env.T_ADM || "") },
      body: JSON.stringify({ system: SYSTEM, customers: ["A", "B", "W"].map(k => ({
        external_id: `${k}-${suf}`, anchors: [{ kind: "phone", value: fones[k] }] })) }) })
    const ij = imp.status === 200 ? await imp.json() : {}
    out.detalhe.import_http = imp.status
    const cid = {}
    for (const r of ij.results || []) cid[r.external_id.split("-")[0]] = r.customer_id
    out.limpeza.customers = Object.values(cid)
    if (!cid.A || !cid.B || !cid.W) { out.erro = "importacao falhou"; return out }

    // ── pendências que exigem OTP, como o delegate as indexa ────────────────
    const pend = async (k, token) => {
      const entry = { session_id: `probe-pid09-proc-${k}-${suf}`, customer_id: cid[k], resume_token: token,
        pool: "probe", skill_id: "probe", suspended_at: new Date().toISOString(), expires_at: null, policy: "offer",
        intent: null, context_preview: {}, root_session_id: "", resume_requires: ["otp"] }
      await redis.hset(`${TENANT}:pending_by_customer:${cid[k]}`, entry.session_id, JSON.stringify(entry))
      await redis.hset(`${TENANT}:resume_tokens`, token, entry.session_id)
      // `expires_at` VENCIDO de propósito: o mcp-server julga a exigência pelo registro (que
      // existe), e o gateway, sem a entrada viva no hash, recusa o token vencido — nenhuma
      // retomada chega a acordar processo, nem quando o portão do mcp falha (imagem antiga).
      await redis.set(`${TENANT}:resume_meta:${token}`, JSON.stringify({
        session_id: entry.session_id, resume_requires: ["otp"], expires_at: "2020-01-01T00:00:00+00:00" }), "EX", 900)
      chaves.push(`${TENANT}:pending_by_customer:${cid[k]}`, `${TENANT}:resume_meta:${token}`,
        `${TENANT}:resume_terminal:${token}`, `${TENANT}:session:${entry.session_id}:status`,
        `session:${entry.session_id}:stream`, `${TENANT}:ctx:journey:${entry.session_id}`)
    }
    await pend("B", tokens.B)
    await pend("W", tokens.W)

    // ── OTP real: S_A prova A, S_B prova B (e B vira `possessed`) ───────────
    const otp = async (sid, k) => {
      const tok = await emite(sid)
      const ch = await chama("otp_challenge", { tenant_id: TENANT, customer_id: cid[k], kind: "phone", value: fones[k], session_token: tok })
      const code = ch.body.dev_code
      const vr = await chama("otp_verify", { tenant_id: TENANT, customer_id: cid[k], kind: "phone", value: fones[k], code: String(code), session_token: tok })
      chaves.push(`${TENANT}:ctx:journey:${sid}`, `${TENANT}:ctx:${sid}`)
      out.detalhe[`otp_${k}`] = { challenge: ch.isError ? ch.body : "ok", verify: vr.body }
      return tok
    }
    const tokA = await otp(sess.A, "A")
    const tokB = await otp(sess.B, "B")

    let r = await pendencia(tokB, fones.B)
    out.detalhe.r_controle_b = r
    c.r_controle_b = levou(r, tokens.B)
    r = await pendencia(tokA, fones.B)
    out.detalhe.r_otp_outro_cliente = r
    c.r_otp_outro_cliente = !levou(r, tokens.B)

    // retomada: tira o token do hash vivo (com o registro vencido, o gateway recusa)
    await redis.hdel(`${TENANT}:resume_tokens`, tokens.B)
    r = await chama("workflow_resume", { resume_token: tokens.B, decision: "input", session_token: tokA })
    out.detalhe.r_resume_outro = r
    c.r_resume_outro_recusa = r.isError && r.body.error === "resume_requires_unproven"
    await redis.hset(`${TENANT}:resume_tokens`, tokens.B, `probe-pid09-proc-B-${suf}`)

    if (SEM_WHATSAPP) return out

    // ── a chegada pelo WhatsApp ──────────────────────────────────────────────
    const tokX = await emite(sess.X)
    r = await pendencia(tokX, fones.W)
    out.detalhe.w_controle_sem_chegada = r
    c.w_controle_sem_chegada = !levou(r, tokens.W) && r.body.verification_required === true

    const webhook = async (fone, assinar = true) => {
      const body = JSON.stringify({ entry: [{ changes: [{ value: { messages: [
        { from: fone.slice(1), id: `wamid.probe${suf}${fone.slice(-2)}`, type: "text", text: { body: "oi" } }] } }] }] })
      const sig = "sha256=" + crypto.createHmac("sha256", process.env.WA_SECRET || "").update(body).digest("hex")
      const resp = await fetch(`${GW}/webhooks/whatsapp`, { method: "POST",
        headers: { "content-type": "application/json", ...(assinar ? { "x-hub-signature-256": sig } : {}) }, body })
      chaves.push(`channel:whatsapp:${fone.slice(1)}:session`)
      return resp.status
    }
    const sessaoDe = async (fone) => {
      for (let i = 0; i < 40; i++) {
        const sid = await redis.get(`channel:whatsapp:${fone.slice(1)}:session`)
        if (sid) {
          const j = await journey(sid)
          if (j.status) return { sid, ...j }
        }
        await sleep(250)
      }
      return { sid: await redis.get(`channel:whatsapp:${fone.slice(1)}:session`) }
    }

    out.detalhe.webhook_w = await webhook(fones.W)
    const w = await sessaoDe(fones.W)
    out.detalhe.w = w
    out.limpeza.sessoes_whatsapp = [w.sid]
    c.w_evidencia_verified = w.status === "verified" && w.customer === cid.W
    const tokW = w.sid ? await emite(w.sid) : ""
    r = await pendencia(tokW, fones.W)
    out.detalhe.w_libera = r
    c.w_libera_sem_otp = levou(r, tokens.W)

    await redis.hdel(`${TENANT}:resume_tokens`, tokens.W)
    r = await chama("workflow_resume", { resume_token: tokens.W, decision: "input", session_token: tokW })
    out.detalhe.w_resume = r
    c.w_resume_passa = r.isError && String(r.body.error || "").startsWith("resume_failed_http_")

    r = await pendencia(tokW, fones.B)
    out.detalhe.w_outro = r
    c.w_outro_cliente_retido = !levou(r, tokens.B)
    await redis.hdel(`${TENANT}:resume_tokens`, tokens.B)
    r = await chama("workflow_resume", { resume_token: tokens.B, decision: "input", session_token: tokW })
    out.detalhe.w_resume_outro = r
    c.w_resume_outro_recusa = r.isError && r.body.error === "resume_requires_unproven"

    out.detalhe.webhook_u = await webhook(fones.U)
    const u = await sessaoDe(fones.U)
    out.detalhe.u = u
    out.limpeza.sessoes_whatsapp.push(u.sid)
    c.u_failed = u.status === "failed" && !u.customer

    const semAss = await webhook(`+55119${5}${suf}`, false)
    await sleep(800)
    c.nao_assinado_400 = semAss === 400 && !(await redis.get(`channel:whatsapp:55119${5}${suf}:session`))
    return out
  } finally {
    for (const t of Object.values(tokens)) await redis.hdel(`${TENANT}:resume_tokens`, t)
    for (const s of out.limpeza.sessoes_whatsapp || []) if (s) chaves.push(`${TENANT}:ctx:journey:${s}`, `${TENANT}:ctx:${s}`)
    if (chaves.length) await redis.del(...new Set(chaves))
    await client.close().catch(() => {})
    redis.disconnect()
  }
}

main().then(o => console.log(JSON.stringify(o))).catch(e => { console.log(JSON.stringify({ erro: String(e && e.stack || e) })); process.exit(0) })
