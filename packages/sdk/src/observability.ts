/**
 * observability.ts
 * Propagação portável de observabilidade.
 * Spec: PlugHub v24.0 seção 4.6f
 *
 * Propaga o plughub.session_id como trace ID raiz para qualquer sistema
 * de observabilidade que o agente já usa (OpenTelemetry, LangSmith, etc.).
 * A correlação entre eventos da plataforma e traces internos é automática.
 */

// ─────────────────────────────────────────────
// Atributos de trace da plataforma
// Spec 4.6f: prefixo plughub.* em todos os atributos
// ─────────────────────────────────────────────

export interface PlugHubTraceAttributes {
  "plughub.session_id":        string
  "plughub.tenant_id":         string
  "plughub.agent_type_id":     string
  "plughub.pool":              string
  "plughub.turn_number":       number
  "plughub.parent_session_id"?: string  // presente em delegações A2A
}

// ─────────────────────────────────────────────
// Interface de span — agnóstica de backend
// ─────────────────────────────────────────────

export interface TraceSpan {
  setAttribute(key: string, value: string | number | boolean): void
  end(): void
}

export interface TracerBackend {
  startSpan(name: string, parentTraceId?: string): TraceSpan
}

// ─────────────────────────────────────────────
// ObservabilityManager
// ─────────────────────────────────────────────

export class ObservabilityManager {
  private _backend: TracerBackend | null = null
  private _attributes: PlugHubTraceAttributes | null = null
  private _active_span: TraceSpan | null = null

  /**
   * Registra um backend de tracing.
   * Opcional — sem backend, apenas loga os atributos.
   */
  useBackend(backend: TracerBackend): this {
    this._backend = backend
    return this
  }

  /**
   * Inicia o contexto de observabilidade para uma conversa.
   * Chamado automaticamente pelo definePlugHubAgent no início de cada turno.
   */
  startTurn(attrs: PlugHubTraceAttributes): void {
    this._attributes = attrs

    if (this._backend) {
      this._active_span = this._backend.startSpan(
        `plughub.turn.${attrs["plughub.agent_type_id"]}`,
        attrs["plughub.session_id"]
      )
      for (const [key, value] of Object.entries(attrs)) {
        this._active_span.setAttribute(key, value as string | number)
      }
    }
  }

  /**
   * Encerra o span ativo.
   * Chamado automaticamente pelo definePlugHubAgent após agent_done.
   */
  endTurn(): void {
    this._active_span?.end()
    this._active_span = null
  }

  /** Acesso aos atributos ativos — útil para instrumentação manual. */
  get attributes(): PlugHubTraceAttributes | null {
    return this._attributes
  }

  /**
   * Retorna os atributos formatados para headers HTTP.
   * Útil para propagar o contexto em chamadas downstream.
   */
  toHeaders(): Record<string, string> {
    if (!this._attributes) return {}
    return {
      "x-plughub-session-id":    this._attributes["plughub.session_id"],
      "x-plughub-tenant-id":     this._attributes["plughub.tenant_id"],
      "x-plughub-agent-type-id": this._attributes["plughub.agent_type_id"],
    }
  }
}

/** Singleton global — um por processo de agente */
export const observability = new ObservabilityManager()
