/**
 * proxy/circuit-breaker.ts
 * Timeout + error_clear fallback for upstream MCP Server calls.
 * Spec: PlugHub v24.0 section 4.6k
 *
 * mode_on_failure: error_clear — returns a clear, structured error to the
 * agent when the upstream times out or fails. Never silently passes.
 */

export class CircuitBreaker {
  private readonly timeoutMs: number

  constructor(timeoutMs: number) {
    this.timeoutMs = timeoutMs
  }

  /**
   * Executes fn with a timeout.
   * On timeout or error, throws a CircuitBreakerError (error_clear mode).
   */
  async execute<T>(fn: () => Promise<T>): Promise<T> {
    const timeoutPromise = new Promise<never>((_, reject) =>
      setTimeout(
        () => reject(new CircuitBreakerError("upstream_timeout", this.timeoutMs)),
        this.timeoutMs,
      )
    )

    try {
      return await Promise.race([fn(), timeoutPromise])
    } catch (err) {
      if (err instanceof CircuitBreakerError) throw err
      throw new CircuitBreakerError(
        "upstream_error",
        this.timeoutMs,
        err instanceof Error ? err.message : String(err),
      )
    }
  }
}

export class CircuitBreakerError extends Error {
  constructor(
    public readonly code:        "upstream_timeout" | "upstream_error",
    public readonly timeoutMs:   number,
    public readonly detail?:     string,
  ) {
    super(
      code === "upstream_timeout"
        ? `MCP upstream did not respond within ${timeoutMs}ms`
        : `MCP upstream error: ${detail ?? "unknown"}`,
    )
    this.name = "CircuitBreakerError"
  }

  /** Structured response body returned to the agent (error_clear mode). */
  toErrorResponse(): Record<string, unknown> {
    return {
      error:      "proxy_circuit_open",
      code:       this.code,
      message:    this.message,
      timeout_ms: this.timeoutMs,
    }
  }
}
