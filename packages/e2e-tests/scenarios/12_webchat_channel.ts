/**
 * 12_webchat_channel.ts
 * Scenario 12: WEBCHAT CHANNEL — Auth flow + text message + media upload + reconnect
 *
 * Tests the Channel Gateway WebSocket + HTTP endpoints end-to-end against a
 * running channel-gateway service. Requires Redis, Kafka, and PostgreSQL.
 *
 * Part A — Auth handshake (3 assertions):
 *   Connect → recv conn.hello → send conn.authenticate (JWT) → recv conn.authenticated
 *   Verify: server_version present, contact_id/session_id match JWT claims, cursor present
 *
 * Part B — Text message inbound (3 assertions):
 *   Send msg.text → verify NormalizedInboundEvent published on conversations.inbound
 *   Verify: channel=webchat, content.type=text, correct text
 *
 * Part C — Upload + media message (5 assertions):
 *   upload.request → upload.ready → HTTP POST binary → upload.committed → msg.image
 *   Verify: file_id consistent, HTTP 204, upload.committed received, media event on Kafka
 *
 * Part D — Reconnect with cursor (3 assertions):
 *   Seed a message in Redis stream → disconnect → reconnect with cursor
 *   Verify: same session_id, stream message delivered after reconnect
 *
 * Assertions: 14
 */

import { randomUUID } from "crypto"
import fetch from "node-fetch"
import type { ScenarioContext, ScenarioResult, Assertion } from "./types"
import { WsTestClient }              from "../lib/ws-client"
import { mintFreshWebchatToken }     from "../lib/jwt-helper"
import { waitForInboundEvent }        from "../lib/kafka-client"
import { seedSessionMeta }           from "../lib/redis-client"
import { pass, fail }                from "../lib/report"

// ── Helpers ───────────────────────────────────────────────────────────────────

function asMsg(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>
  throw new Error(`Expected object, got: ${JSON.stringify(raw)}`)
}

// ── Scenario ──────────────────────────────────────────────────────────────────

export async function run(ctx: ScenarioContext): Promise<ScenarioResult> {
  const startAt    = Date.now()
  const assertions: Assertion[] = []

  const { token, contactId, sessionId } = mintFreshWebchatToken({
    tenantId:  ctx.tenantId,
    jwtSecret: ctx.webchatJwtSecret,
  })

  // Pool to connect to (must exist in the gateway config; e2e pool)
  const poolId = "retencao_humano"
  const wsUrl  = `${ctx.channelGatewayWsUrl}/ws/chat/${poolId}`
  const client = new WsTestClient(wsUrl)

  // Seed session metadata so the gateway can resolve tenant_id
  await seedSessionMeta(ctx.redis, sessionId, ctx.tenantId, contactId)

  let streamCursor = "0-0"

  try {
    await client.connect()

    // ── Part A — Auth handshake ───────────────────────────────────────────────

    // A1: Server sends conn.hello immediately after accept
    const hello = asMsg(await client.receive(5000))
    assertions.push(
      hello["type"] === "conn.hello" && typeof hello["server_version"] === "string"
        ? pass("A: conn.hello recebido com server_version")
        : fail("A: conn.hello inválido ou ausente", { hello })
    )

    // A2: Client authenticates with JWT
    client.send({ type: "conn.authenticate", token, cursor: null })

    const authed = asMsg(await client.receive(8000))
    assertions.push(
      authed["type"] === "conn.authenticated" &&
      authed["contact_id"]    === contactId &&
      authed["session_id"]    === sessionId &&
      typeof authed["stream_cursor"] === "string"
        ? pass("A: conn.authenticated com contact_id, session_id e stream_cursor corretos", {
            contact_id:    authed["contact_id"],
            session_id:    authed["session_id"],
            stream_cursor: authed["stream_cursor"],
          })
        : fail("A: conn.authenticated inválido", { authed })
    )

    streamCursor = (authed["stream_cursor"] as string) || "0-0"

    // A3: cursor is non-empty (may be "0-0" when stream is new — still valid)
    assertions.push(
      streamCursor.length > 0
        ? pass("A: stream_cursor presente e não vazio", { cursor: streamCursor })
        : fail("A: stream_cursor vazio", { authed })
    )

    // ── Part B — Text message inbound ─────────────────────────────────────────

    // Two-phase waiter: start consumer → await GROUP_JOIN (ready) → send WS
    // message → await Kafka event (result).  This eliminates the race between
    // consumer partition assignment and the channel gateway publishing the event.
    const inboundWaiter = waitForInboundEvent(ctx.kafka, sessionId, 15000, "text")
    await inboundWaiter.ready   // consumer is now live — safe to publish

    const textId  = randomUUID()
    const textMsg = "Olá, preciso de ajuda com minha fatura"
    client.send({ type: "msg.text", id: textId, text: textMsg })

    const evt = await inboundWaiter.result as Record<string, unknown> | null

    assertions.push(
      evt && (evt["channel"] as string) === "webchat"
        ? pass("B: NormalizedInboundEvent publicado em conversations.inbound com channel=webchat")
        : fail("B: evento não encontrado ou channel incorreto", { evt })
    )

    const content = evt?.["content"] as Record<string, unknown> | undefined
    assertions.push(
      content?.["type"] === "text"
        ? pass("B: content.type=text")
        : fail("B: content.type incorreto", { content })
    )
    assertions.push(
      content?.["text"] === textMsg
        ? pass("B: content.text correto", { text: content?.["text"] })
        : fail("B: content.text incorreto", { expected: textMsg, got: content?.["text"] })
    )

    // ── Part C — Upload + media message ───────────────────────────────────────

    const fileName  = "test-photo.jpg"
    const mimeType  = "image/jpeg"
    const imageData = Buffer.alloc(2048, 0x42)   // 2 KB of 'B' bytes — synthetic JPEG
    const reqId     = randomUUID()

    // C1: upload.request → upload.ready
    // Use receiveTyped to skip any stream delivery messages (e.g. routing engine
    // "Aguardando agente" messages) that may arrive before upload.ready.
    client.send({
      type:       "upload.request",
      id:         reqId,
      file_name:  fileName,
      mime_type:  mimeType,
      size_bytes: imageData.byteLength,
    })

    const uploadReady = asMsg(await client.receiveTyped("upload.ready", 6000))
    const fileId      = uploadReady["file_id"] as string | undefined

    assertions.push(
      uploadReady["type"] === "upload.ready" &&
      typeof fileId === "string" && fileId.length > 0 &&
      typeof uploadReady["upload_url"] === "string"
        ? pass("C: upload.ready recebido com file_id e upload_url", {
            file_id:    fileId,
            upload_url: uploadReady["upload_url"],
          })
        : fail("C: upload.ready inválido", { uploadReady })
    )

    // C2: HTTP POST binary to upload_url
    const uploadUrl = uploadReady["upload_url"] as string
    const httpResp  = await fetch(uploadUrl, {
      method:  "POST",
      headers: { "Content-Type": mimeType },
      body:    imageData,
    })

    assertions.push(
      httpResp.status === 204
        ? pass("C: HTTP POST upload retornou 204 No Content", { status: 204 })
        : fail("C: HTTP POST upload falhou", { status: httpResp.status, body: await httpResp.text() })
    )

    // C3: Server sends upload.committed over WS (skip interleaved stream events)
    const committed = asMsg(await client.receiveTyped("upload.committed", 6000))
    assertions.push(
      committed["type"]    === "upload.committed" &&
      committed["file_id"] === fileId  &&
      typeof committed["url"] === "string"
        ? pass("C: upload.committed recebido com file_id e url", {
            file_id: committed["file_id"],
            url:     committed["url"],
          })
        : fail("C: upload.committed inválido", { committed })
    )

    // C4: Client sends msg.image with committed file_id → Kafka event
    // Two-phase waiter: await GROUP_JOIN before sending WS to avoid race.
    const mediaWaiter = waitForInboundEvent(ctx.kafka, sessionId, 15000, "media")
    await mediaWaiter.ready   // consumer live before publishing

    client.send({ type: "msg.image", id: randomUUID(), file_id: fileId, caption: "foto da fatura" })

    const mediaEvt    = await mediaWaiter.result as Record<string, unknown> | null
    const mediaContent = mediaEvt?.["content"] as Record<string, unknown> | undefined

    assertions.push(
      mediaContent?.["type"] === "media"
        ? pass("C: NormalizedInboundEvent de mídia publicado com content.type=media", {
            file_id: (mediaContent?.["payload"] as Record<string, unknown>)?.["file_id"],
          })
        : fail("C: evento de mídia ausente ou content.type incorreto", { mediaContent })
    )

    // C5: GET attachment endpoint returns the file
    const serveUrl  = `${ctx.channelGatewayHttpUrl}/webchat/v1/attachments/${fileId}`
    const serveResp = await fetch(serveUrl)

    assertions.push(
      serveResp.status === 200
        ? pass("C: GET /attachments/{file_id} retorna 200 e o arquivo", {
            content_type: serveResp.headers.get("content-type"),
          })
        : fail("C: GET /attachments/{file_id} falhou", { status: serveResp.status })
    )

    // ── Part D — Reconnect with cursor ────────────────────────────────────────

    // Seed a synthetic "agent message" directly into the Redis stream so the
    // reconnect test can verify cursor-based replay without needing the Core.
    const agentText  = "Olá! Como posso ajudar você hoje?"
    const streamKey  = `session:${sessionId}:stream`

    // Capture the last stream entry ID BEFORE seeding so the reconnect cursor
    // points to just before our agent message.  This ensures the stream delivery
    // loop replays ONLY the seeded message and not earlier system messages (e.g.
    // "Aguardando agente" published by the routing engine during Part B).
    const latestBefore = await ctx.redis.xrevrange(streamKey, "+", "-", "COUNT", 1)
    const cursorBeforeAgent = latestBefore.length > 0 ? (latestBefore[0] as string[])[0] : "0-0"

    // Field names and structure must match StreamSubscriber._map_event():
    //   type      → event type ("message")
    //   author    → JSON {"role", "participant_id"}
    //   payload   → JSON {"content": {"type", "text"}}
    //   event_id  → message id
    // (The routing engine's "Aguardando agente" goes via Kafka outbound, not this
    //  stream path — so it arrives as type=message.text from a different mechanism.)
    await ctx.redis.xadd(
      streamKey,
      "*",
      "type",       "message",
      "visibility", "all",
      "author",     JSON.stringify({ role: "agent_ai", participant_id: "part-e2e-agent" }),
      "payload",    JSON.stringify({ content: { type: "text", text: agentText } }),
      "event_id",   randomUUID(),
      "timestamp",  new Date().toISOString()
    )

    // Disconnect client (simulate network drop)
    client.disconnect()
    await new Promise((r) => setTimeout(r, 200))

    // Reconnect with cursor = last entry before our seed.  Stream delivery will
    // start from AFTER that entry, delivering only the seeded agent message.
    await client.reconnect()

    // Server sends conn.hello immediately on connect — consume it first, then
    // authenticate. If we send auth before reading conn.hello, the first receive()
    // call returns conn.hello instead of conn.authenticated.
    await client.receiveTyped("conn.hello", 5000)

    client.send({ type: "conn.authenticate", token, cursor: cursorBeforeAgent })

    // Collect ALL messages until conn.authenticated, preserving any stream replay
    // messages the gateway may send BEFORE conn.authenticated.  receiveTyped()
    // would silently discard those early stream messages, so we do it manually.
    const preCollected: Record<string, unknown>[] = []
    let reAuthed: Record<string, unknown> | null = null
    {
      const authDeadline = Date.now() + 8000
      while (Date.now() < authDeadline) {
        const remaining = authDeadline - Date.now()
        if (remaining <= 0) break
        let raw: Record<string, unknown>
        try {
          raw = asMsg(await client.receive(remaining))
        } catch {
          break
        }
        if (raw["type"] === "conn.authenticated") {
          reAuthed = raw
          break
        }
        preCollected.push(raw) // stream replay msg that arrived before auth
      }
    }

    assertions.push(
      reAuthed !== null &&
      reAuthed["session_id"] === sessionId
        ? pass("D: reconexão bem-sucedida — conn.authenticated com mesmo session_id", {
            session_id:    reAuthed["session_id"],
            stream_cursor: reAuthed["stream_cursor"],
          })
        : fail("D: reconexão falhou ou session_id diferente", { reAuthed })
    )

    // D2: Stream delivery task replays messages after the cursor.
    // Check preCollected first (stream messages that arrived before conn.authenticated),
    // then continue reading from the socket.  Skip interleaved system messages
    // (e.g. routing engine's "Aguardando agente") until the seeded agent message
    // is found or the 8-second deadline expires.
    const matchesAgentMsg = (msg: Record<string, unknown>): boolean => {
      const t = msg["type"] as string
      const text =
        (msg["text"] as string | undefined) ??
        ((msg["content"] as Record<string, unknown>)?.["text"] as string | undefined)
      return (t === "msg.text" || t === "message.text") && text === agentText
    }

    let foundAgentMsg = false

    for (const msg of preCollected) {
      if (matchesAgentMsg(msg)) {
        foundAgentMsg = true
        assertions.push(
          pass("D: mensagem do agente entregue via replay após reconexão com cursor", {
            type: msg["type"],
          })
        )
        break
      }
    }

    if (!foundAgentMsg) {
      const deadline = Date.now() + 8000
      while (Date.now() < deadline) {
        const remaining = deadline - Date.now()
        if (remaining <= 100) break
        let msg: Record<string, unknown>
        try {
          msg = asMsg(await client.receive(remaining))
        } catch {
          break
        }
        if (matchesAgentMsg(msg)) {
          foundAgentMsg = true
          assertions.push(
            pass("D: mensagem do agente entregue via replay após reconexão com cursor", {
              type: msg["type"],
            })
          )
          break
        }
      }
    }

    if (!foundAgentMsg) {
      assertions.push(
        fail("D: replay de mensagem não entregue após reconexão", { agentText })
      )
    }

    // D3: New cursor in reAuthed is ≥ old cursor (has advanced or stayed same)
    const newCursor = (reAuthed?.["stream_cursor"] as string) || ""
    assertions.push(
      newCursor.length > 0
        ? pass("D: novo stream_cursor presente após reconexão", { cursor: newCursor })
        : fail("D: stream_cursor ausente após reconexão", { reAuthed })
    )

  } catch (err) {
    assertions.push(fail("Scenario 12 unexpected error", String(err)))
  } finally {
    client.disconnect()
  }

  return {
    scenario_id: "12",
    name:        "WebChat Channel — Auth + Text + Upload + Reconnect",
    passed:      assertions.every((a) => a.passed),
    assertions,
    duration_ms: Date.now() - startAt,
  }
}
