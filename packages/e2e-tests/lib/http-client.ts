/**
 * http-client.ts
 * HTTP clients for agent-registry, rules-engine, and skill-flow-service.
 */

async function post(url: string, body: unknown, headers?: Record<string, string>): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`POST ${url} → ${res.status}: ${text}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function get(url: string, headers?: Record<string, string>): Promise<unknown> {
  const res = await fetch(url, { headers });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`GET ${url} → ${res.status}: ${text}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function patch(url: string, body: unknown, headers?: Record<string, string>): Promise<unknown> {
  const res = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`PATCH ${url} → ${res.status}: ${text}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent Registry Client
// ─────────────────────────────────────────────────────────────────────────────

export class RegistryClient {
  constructor(
    private readonly baseUrl: string,
    private readonly tenantId: string
  ) {}

  private headers(): Record<string, string> {
    return {
      "x-tenant-id": this.tenantId,
      "x-user-id": "e2e-runner",
    };
  }

  async createPool(body: object): Promise<unknown> {
    try {
      return await post(`${this.baseUrl}/v1/pools`, body, this.headers());
    } catch (err) {
      // Ignore 409 Conflict (already exists)
      if (err instanceof Error && err.message.includes("409")) return null;
      throw err;
    }
  }

  async createAgentType(body: object): Promise<unknown> {
    try {
      return await post(`${this.baseUrl}/v1/agent-types`, body, this.headers());
    } catch (err) {
      if (err instanceof Error && err.message.includes("409")) return null;
      throw err;
    }
  }

  async createSkill(body: object): Promise<unknown> {
    try {
      return await post(`${this.baseUrl}/v1/skills`, body, this.headers());
    } catch (err) {
      if (err instanceof Error && err.message.includes("409")) return null;
      throw err;
    }
  }

  async getPool(poolId: string): Promise<unknown> {
    return get(`${this.baseUrl}/v1/pools/${poolId}`, this.headers());
  }

  async listPools(): Promise<unknown> {
    return get(`${this.baseUrl}/v1/pools`, this.headers());
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Rules Engine Client
// ─────────────────────────────────────────────────────────────────────────────

export class RulesEngineClient {
  constructor(private readonly baseUrl: string) {}

  async createRule(body: object): Promise<{ rule_id: string; status: string }> {
    return (await post(`${this.baseUrl}/rules`, body)) as {
      rule_id: string;
      status: string;
    };
  }

  async updateRuleStatus(
    ruleId: string,
    status: string,
    tenantId: string
  ): Promise<unknown> {
    return patch(
      `${this.baseUrl}/rules/${ruleId}/status?tenant_id=${encodeURIComponent(tenantId)}`,
      { status }
    );
  }

  async getRule(ruleId: string, tenantId: string): Promise<unknown> {
    return get(
      `${this.baseUrl}/rules/${ruleId}?tenant_id=${encodeURIComponent(tenantId)}`
    );
  }

  async listRules(tenantId: string, status?: string): Promise<unknown[]> {
    const qs = status
      ? `tenant_id=${encodeURIComponent(tenantId)}&status=${encodeURIComponent(status)}`
      : `tenant_id=${encodeURIComponent(tenantId)}`;
    return (await get(`${this.baseUrl}/rules?${qs}`)) as unknown[];
  }

  async evaluate(body: {
    session_id: string;
    tenant_id: string;
    turn_id?: string;
  }): Promise<{ should_escalate: boolean; rule_id?: string; pool_target?: string }> {
    return (await post(`${this.baseUrl}/evaluate`, body)) as {
      should_escalate: boolean;
      rule_id?: string;
      pool_target?: string;
    };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Skill Flow Service Client
// ─────────────────────────────────────────────────────────────────────────────

export class SkillFlowClient {
  constructor(private readonly baseUrl: string) {}

  async execute(body: object): Promise<
    | { outcome: string; pipeline_state: unknown }
    | { error: string; active_job_id?: string }
  > {
    return (await post(`${this.baseUrl}/execute`, body)) as
      | { outcome: string; pipeline_state: unknown }
      | { error: string; active_job_id?: string };
  }

  async getPipeline(tenantId: string, sessionId: string): Promise<unknown | null> {
    try {
      return await get(`${this.baseUrl}/pipeline/${encodeURIComponent(tenantId)}/${encodeURIComponent(sessionId)}`);
    } catch (err) {
      if (err instanceof Error && err.message.includes("404")) return null;
      throw err;
    }
  }

  async health(): Promise<boolean> {
    try {
      await get(`${this.baseUrl}/health`);
      return true;
    } catch {
      return false;
    }
  }
}
