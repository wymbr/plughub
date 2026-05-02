/**
 * lifecycle.ts
 * Gerenciamento automático do ciclo de vida do agente.
 * Spec: PlugHub v24.0 seção 4.5 + 9.4
 *
 * Ciclo: agent_login → agent_ready → agent_busy → agent_done
 * O SDK gerencia todas as transições — o agente não precisa chamar nenhuma delas.
 */

import type { ContextPackage, AgentDone } from "@plughub/schemas"

// ─────────────────────────────────────────────
// Tipos do ciclo de vida
// ─────────────────────────────────────────────

export interface AgentLoginPayload {
  agent_type_id: string
  tenant_id:     string
  instance_id:   string
}

export interface AgentLoginResponse {
  session_token:   string
  token_expires_at: string
  instance_id:     string
}

export interface AgentReadyPayload {
  instance_id:   string
  session_token: string
}

export interface AgentBusyPayload {
  instance_id:    string
  session_token:  string
  session_id:     string
  customer_id:    string
}

// ─────────────────────────────────────────────
// LifecycleManager
// ─────────────────────────────────────────────

export interface LifecycleManagerConfig {
  /** URL base do mcp-server-plughub */
  server_url:    string
  agent_type_id: string
  tenant_id:     string
  /** Margem em ms antes de expirar para renovar o JWT */
  token_refresh_margin_ms?: number
}

export class LifecycleManager {
  private _session_token:     string | null = null
  private _token_expires_at:  Date   | null = null
  private _instance_id:       string | null = null
  private _refresh_timer:     ReturnType<typeof setTimeout> | null = null

  readonly config: LifecycleManagerConfig

  constructor(config: LifecycleManagerConfig) {
    this.config = {
      token_refresh_margin_ms: 60_000, // 1 min antes de expirar
      ...config,
    }
  }

  get instance_id(): string {
    if (!this._instance_id) throw new Error("LifecycleManager: agent_login não foi chamado")
    return this._instance_id
  }

  get session_token(): string {
    if (!this._session_token) throw new Error("LifecycleManager: agent_login não foi chamado")
    return this._session_token
  }

  /** Registra a instância na plataforma. Primeiro passo obrigatório. */
  async login(): Promise<void> {
    const instanceId = `${this.config.agent_type_id}_${crypto.randomUUID().slice(0, 8)}`

    const response = await this._call<AgentLoginResponse>("agent_login", {
      agent_type_id: this.config.agent_type_id,
      tenant_id:     this.config.tenant_id,
      instance_id:   instanceId,
    } satisfies AgentLoginPayload)

    this._session_token   = response.session_token
    this._token_expires_at = new Date(response.token_expires_at)
    this._instance_id     = response.instance_id

    this._scheduleTokenRefresh()
  }

  /** Coloca a instância na fila de alocação. */
  async ready(): Promise<void> {
    await this._call("agent_ready", {
      instance_id:  this.instance_id,
      session_token: this.session_token,
    } satisfies AgentReadyPayload)
  }

  /** Marca a instância como ocupada com uma conversa. */
  async busy(session_id: string, customer_id: string): Promise<void> {
    await this._call("agent_busy", {
      instance_id:   this.instance_id,
      session_token: this.session_token,
      session_id,
      customer_id,
    } satisfies AgentBusyPayload)
  }

  /** Sinaliza conclusão da conversa. */
  async done(payload: Omit<AgentDone, "agent_id" | "completed_at">): Promise<void> {
    await this._call("agent_done", {
      ...payload,
      agent_id:     this.instance_id,
      completed_at: new Date().toISOString(),
    })
  }

  /** Graceful shutdown — drena conversas ativas antes de deslogar. */
  async logout(): Promise<void> {
    if (this._refresh_timer) clearTimeout(this._refresh_timer)
    if (!this._session_token) return
    await this._call("agent_logout", {
      instance_id:  this.instance_id,
      session_token: this.session_token,
    }).catch(() => {}) // best-effort no shutdown
  }

  /** Registra SIGTERM para graceful shutdown automático. */
  registerShutdownHook(): void {
    process.on("SIGTERM", () => {
      this.logout().finally(() => process.exit(0))
    })
  }

  private _scheduleTokenRefresh(): void {
    if (!this._token_expires_at) return
    const margin = this.config.token_refresh_margin_ms ?? 60_000
    const refreshIn = this._token_expires_at.getTime() - Date.now() - margin
    if (refreshIn <= 0) return

    this._refresh_timer = setTimeout(async () => {
      try {
        await this.login() // re-login renova o token
      } catch {
        // próxima chamada vai falhar com token expirado e alertar
      }
    }, refreshIn)
  }

  private async _call<T = void>(tool: string, input: unknown): Promise<T> {
    const response = await fetch(`${this.config.server_url}/tools/${tool}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(input),
    })
    if (!response.ok) {
      const error = await response.text()
      throw new Error(`PlugHub lifecycle error [${tool}]: ${response.status} — ${error}`)
    }
    return response.json() as Promise<T>
  }
}
