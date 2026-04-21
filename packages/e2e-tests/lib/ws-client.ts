/**
 * ws-client.ts
 * Promise-based WebSocket test client built on the `ws` package.
 * Suitable for E2E tests that need to drive a WS conversation sequentially.
 */

import WebSocket from "ws"

export class WsTestClient {
  private ws: WebSocket
  private readonly url: string
  private queue: Array<{
    resolve: (msg: unknown) => void
    reject: (err: Error) => void
  }> = []
  private messageBuffer: unknown[] = []
  private closed = false

  constructor(url: string) {
    this.url   = url
    this.ws    = this._createSocket()
  }

  private _createSocket(): WebSocket {
    const ws = new WebSocket(this.url)

    ws.on("message", (data: Buffer | string) => {
      let parsed: unknown
      try {
        parsed = JSON.parse(data.toString())
      } catch {
        parsed = data.toString()
      }

      const waiter = this.queue.shift()
      if (waiter) {
        waiter.resolve(parsed)
      } else {
        this.messageBuffer.push(parsed)
      }
    })

    ws.on("error", (err: Error) => {
      this.queue.forEach((w) => w.reject(err))
      this.queue = []
    })

    ws.on("close", () => {
      this.closed = true
      const err = new Error("WebSocket closed")
      this.queue.forEach((w) => w.reject(err))
      this.queue = []
    })

    return ws
  }

  /** Wait until the underlying socket is OPEN. */
  async connect(timeoutMs = 5000): Promise<void> {
    if (this.ws.readyState === WebSocket.OPEN) return

    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error(`WS connect timeout after ${timeoutMs}ms`)),
        timeoutMs
      )
      this.ws.once("open",  () => { clearTimeout(timer); resolve() })
      this.ws.once("error", (err) => { clearTimeout(timer); reject(err) })
    })
  }

  /** Wait for the next incoming JSON message. */
  async receive(timeoutMs = 5000): Promise<unknown> {
    if (this.messageBuffer.length > 0) {
      return this.messageBuffer.shift()!
    }

    return new Promise((resolve, reject) => {
      let fulfilled = false

      const timer = setTimeout(() => {
        if (fulfilled) return
        fulfilled = true
        this.queue = this.queue.filter((w) => w.resolve !== onResolve)
        reject(new Error(`WS receive timeout after ${timeoutMs}ms`))
      }, timeoutMs)

      const onResolve = (msg: unknown) => {
        if (fulfilled) return
        fulfilled = true
        clearTimeout(timer)
        resolve(msg)
      }
      const onReject = (err: Error) => {
        if (fulfilled) return
        fulfilled = true
        clearTimeout(timer)
        reject(err)
      }

      this.queue.push({ resolve: onResolve, reject: onReject })
    })
  }

  /** Send a JSON-serialisable payload. */
  send(data: unknown): void {
    this.ws.send(JSON.stringify(data))
  }

  /** Close the current socket WITHOUT creating a new one (simulates client disconnect). */
  disconnect(): void {
    this.closed = true
    this.ws.close()
  }

  /**
   * Reconnect — dispose the current socket and open a new connection to the
   * same URL. Resets the message queue and buffer.
   */
  async reconnect(timeoutMs = 5000): Promise<void> {
    this.ws.removeAllListeners()
    this.ws.close()
    this.queue         = []
    this.messageBuffer = []
    this.closed        = false
    this.ws            = this._createSocket()
    await this.connect(timeoutMs)
  }

  get isOpen(): boolean {
    return this.ws.readyState === WebSocket.OPEN
  }
}
