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
  /**
   * `serviceToken` — credencial de SERVIÇO do agent-registry. As MUTAÇÕES de
   * `/v1/pools` e `/v1/skills` são gateadas por `requireResourceWrite`, que
   * aceita `x-service-token` OU `Bearer` + ABAC `config.resources`. O runner e2e
   * é caller interno e não carrega JWT de usuário, então usa o token de serviço
   * — o MESMO padrão, e a MESMA variável de ambiente, de `mcp-server/server.ts`
   * e `tools/deploy.ts`.
   *
   * Sem ele o seed morria em `POST /v1/skills` com 401 desde que o gate existe,
   * travando a suíte INTEIRA contra o demo (TODO §101 → e2e → §1055). Note que
   * `/v1/agent-types` NÃO é gateado (`agent-registry/src/app.ts` §43-48) — só
   * pools e skills passam por aqui precisando da credencial.
   *
   * Default por env e não por `config` de propósito: `fixtures/seed_demo.ts`
   * monta um objeto de config próprio, e um campo em `runner.ts` seria uma
   * segunda fonte que aquele caminho não enxerga.
   */
  constructor(
    private readonly baseUrl: string,
    private readonly tenantId: string,
    private readonly serviceToken: string = process.env["AGENT_REGISTRY_SERVICE_TOKEN"] ?? ""
  ) {}

  private headers(): Record<string, string> {
    return {
      "x-tenant-id": this.tenantId,
      "x-user-id": "e2e-runner",
      // Omitido quando vazio: se o destino não configurou `service_token` nem
      // `jwt_secret`, o gate é no-op e header vazio só faz ruído.
      ...(this.serviceToken ? { "x-service-token": this.serviceToken } : {}),
    };
  }

  /**
   * Mutação com 409 tolerado (already exists) e 401/403 ANOTADO.
   *
   * A anotação existe porque status sozinho não distingue "não mandei
   * credencial" de "mandei a credencial errada" — e foi exatamente essa
   * ambiguidade que deixou o §101 aberto. Um 401 sem `token=…` no texto manda
   * quem depura procurar no lugar errado.
   */
  private async createIgnoringConflict(path: string, body: object): Promise<unknown> {
    try {
      return await post(`${this.baseUrl}${path}`, body, this.headers());
    } catch (err) {
      if (!(err instanceof Error)) throw err;
      if (err.message.includes("409")) return null;  // already exists
      if (err.message.includes("→ 401") || err.message.includes("→ 403")) {
        throw new Error(
          `${err.message} [x-service-token=${
            this.serviceToken
              ? "enviado"
              : "AUSENTE (env AGENT_REGISTRY_SERVICE_TOKEN vazia)"
          }]`
        );
      }
      throw err;
    }
  }

  async createPool(body: object): Promise<unknown> {
    return this.createIgnoringConflict("/v1/pools", body);
  }

  async createAgentType(body: object): Promise<unknown> {
    return this.createIgnoringConflict("/v1/agent-types", body);
  }

  async createSkill(body: object): Promise<unknown> {
    return this.createIgnoringConflict("/v1/skills", body);
  }

  async getPool(poolId: string): Promise<unknown> {
    return get(`${this.baseUrl}/v1/pools/${poolId}`, this.headers());
  }

  async listPools(): Promise<unknown> {
    return get(`${this.baseUrl}/v1/pools`, this.headers());
  }

  async listAgentTypes(): Promise<{ agent_types: AgentTypeRecord[]; total: number }> {
    return get(`${this.baseUrl}/v1/agent-types`, this.headers()) as Promise<{
      agent_types: AgentTypeRecord[];
      total: number;
    }>;
  }

  async getAgentType(agentTypeId: string): Promise<AgentTypeRecord> {
    return get(`${this.baseUrl}/v1/agent-types/${agentTypeId}`, this.headers()) as Promise<AgentTypeRecord>;
  }
}

export interface AgentTypeRecord {
  agent_type_id:           string;
  framework:               string;
  execution_model:         string;
  max_concurrent_sessions: number;
  pools:                   Array<{ pool_id: string } | string>;
  [key: string]: unknown;
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

// ─────────────────────────────────────────────────────────────────────────────
// Calendar API Client (Arc 4)
// ─────────────────────────────────────────────────────────────────────────────

export class CalendarClient {
  constructor(private readonly baseUrl: string) {}

  async health(): Promise<{ status: string }> {
    return (await get(`${this.baseUrl}/v1/health`)) as { status: string };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Workflow API Client (Arc 4)
// ─────────────────────────────────────────────────────────────────────────────

export type WorkflowInstance = {
  id: string;
  status: string;
  flow_id: string;
  tenant_id: string;
  resume_token?: string;
  resume_expires_at?: string;
  current_step?: string;
  outcome?: string;
  [key: string]: unknown;
};

export class WorkflowClient {
  constructor(private readonly baseUrl: string) {}

  async health(): Promise<{ status: string; postgres: string }> {
    return (await get(`${this.baseUrl}/v1/health`)) as {
      status: string;
      postgres: string;
    };
  }

  async trigger(body: {
    tenant_id: string;
    flow_id: string;
    trigger_type?: string;
    session_id?: string;
    pool_id?: string;
    context?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }): Promise<WorkflowInstance> {
    return (await post(`${this.baseUrl}/v1/workflow/trigger`, body)) as WorkflowInstance;
  }

  async getInstance(instanceId: string): Promise<WorkflowInstance> {
    return (await get(
      `${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}`
    )) as WorkflowInstance;
  }

  async listInstances(
    tenantId: string,
    params?: { status?: string; flow_id?: string; limit?: number; offset?: number }
  ): Promise<WorkflowInstance[]> {
    const qs = new URLSearchParams({ tenant_id: tenantId, ...params as Record<string, string> }).toString();
    return (await get(`${this.baseUrl}/v1/workflow/instances?${qs}`)) as WorkflowInstance[];
  }

  async persistSuspend(
    instanceId: string,
    body: {
      step_id: string;
      resume_token: string;
      reason: string;
      timeout_hours?: number;
      business_hours?: boolean;
      entity_type?: string;
      entity_id?: string;
      pipeline_state?: Record<string, unknown>;
      metadata?: Record<string, unknown>;
    }
  ): Promise<{ resume_expires_at: string; instance: WorkflowInstance }> {
    return (await post(
      `${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/persist-suspend`,
      body
    )) as { resume_expires_at: string; instance: WorkflowInstance };
  }

  async resume(body: {
    token: string;
    decision: "approved" | "rejected" | "input" | "timeout";
    payload?: Record<string, unknown>;
  }): Promise<{
    instance_id: string;
    flow_id: string;
    decision: string;
    wait_duration_ms: number;
    instance: WorkflowInstance;
  }> {
    return (await post(`${this.baseUrl}/v1/workflow/resume`, body)) as {
      instance_id: string;
      flow_id: string;
      decision: string;
      wait_duration_ms: number;
      instance: WorkflowInstance;
    };
  }

  async complete(
    instanceId: string,
    body: { outcome: string; pipeline_state?: Record<string, unknown> }
  ): Promise<WorkflowInstance> {
    return (await post(
      `${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/complete`,
      body
    )) as WorkflowInstance;
  }

  async fail(instanceId: string, error: string): Promise<WorkflowInstance> {
    return (await post(
      `${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/fail`,
      { error }
    )) as WorkflowInstance;
  }

  async cancel(
    instanceId: string,
    body?: { cancelled_by?: string; reason?: string }
  ): Promise<WorkflowInstance> {
    return (await post(
      `${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/cancel`,
      body ?? {}
    )) as WorkflowInstance;
  }

  // ── Collect step methods ───────────────────────────────────────────────────

  async persistCollect(
    instanceId: string,
    body: {
      step_id:        string;
      collect_token:  string;
      target:         { type: string; id: string };
      channel:        string;
      interaction:    string;
      prompt:         string;
      options?:       unknown[];
      fields?:        unknown[];
      scheduled_at?:  string;
      delay_hours?:   number;
      timeout_hours?: number;
      business_hours?: boolean;
      campaign_id?:   string;
    }
  ): Promise<{
    collect_token:  string;
    send_at:        string;
    expires_at:     string;
    status:         string;
    instance:       WorkflowInstance;
  }> {
    return (await post(
      `${this.baseUrl}/v1/workflow/instances/${encodeURIComponent(instanceId)}/collect/persist`,
      body
    )) as {
      collect_token: string;
      send_at: string;
      expires_at: string;
      status: string;
      instance: WorkflowInstance;
    };
  }

  async collectRespond(body: {
    collect_token: string;
    response_data: Record<string, unknown>;
    channel?:      string;
    session_id?:   string;
  }): Promise<{
    collect_token:   string;
    status:          string;
    elapsed_ms:      number;
    instance_id:     string;
    workflow_resumed: boolean;
  }> {
    return (await post(
      `${this.baseUrl}/v1/workflow/collect/respond`,
      body
    )) as {
      collect_token:    string;
      status:           string;
      elapsed_ms:       number;
      instance_id:      string;
      workflow_resumed: boolean;
    };
  }

  async listCampaignCollects(
    campaignId: string,
    tenantId: string
  ): Promise<unknown[]> {
    return (await get(
      `${this.baseUrl}/v1/workflow/campaigns/${encodeURIComponent(campaignId)}/collects?tenant_id=${encodeURIComponent(tenantId)}`
    )) as unknown[];
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
